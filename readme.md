# Polymarket Ingestion Module — Technical Design

## 1. Purpose & Scope

This module is responsible for producing a local, queryable, time-consistent dataset of Polymarket activity sufficient to support all downstream profiling, aggregation, and backtesting work. It is explicitly **read-only** with respect to Polymarket: no orders, no signing, no wallet interaction.

The module must deliver, for a configurable set of resolved markets:
1. Market metadata (question, category, resolution date, resolution outcome).
2. The full trade ledger for each market (every fill, with timestamp, price, size, side, taker wallet, maker wallet).
3. Per-wallet trade history across all markets that wallet has touched within the dataset's scope.
4. A mid-price time series for each market at a resolution sufficient to reconstruct the "market price at moment of trade" for every trade.

It must do this across the **CTF Exchange V1 / V2 contract boundary** (April 28, 2026) as a single unified dataset.

## 2. Non-Goals

- Real-time / streaming ingestion. Batch only.
- Order book reconstruction beyond what is needed for mid-price recovery. Full L2 replay is out of scope.
- Wallet identity resolution (linking multiple wallets to one human). That belongs to the profiling layer.
- Any handling of unresolved markets. The ingestion writes resolution outcome as a required field; unresolved markets are skipped.

## 3. Data Sources

Three external data sources, in priority order:

### 3.1 Polymarket Gamma API (`gamma-api.polymarket.com`)
- **Role**: Market metadata and resolution outcomes. Authoritative source for "what markets exist and how did they resolve."
- **Auth**: None for read endpoints.
- **Key endpoints**: market listing (paginated, filterable by `closed=true`), market detail by condition ID.
- **Reliability**: High. Public, stable, no rate limit issues at research scale.

### 3.2 Polymarket Data API (`data-api.polymarket.com`)
- **Role**: Trade history queryable by market OR by user wallet. Primary source for the trade ledger.
- **Auth**: None for the endpoints used here (`/trades`, `/positions`, `/activity`).
- **Reliability**: Subject to undocumented rate limits. Empirically, single-threaded polite querying (≤2 req/sec) is stable; bursts cause 429s.
- **Quirks**: Pagination uses cursor-based offsets; total counts not always returned, so termination must rely on empty responses.

### 3.3 Polygon JSON-RPC + CTF Exchange contract event logs
- **Role**: Fallback and authoritative cross-check for trade data; primary source for pre-April-28 V1 history if the Data API truncates.
- **Auth**: Free public RPC endpoints work for low-rate access; a paid RPC (Alchemy, QuickNode) is recommended for full backfills.
- **Reliability**: Fully reliable; rate limits depend on RPC provider tier.
- **V1 vs V2**: Different contract addresses, different event signatures. The ingestion must register both ABIs and route by contract address.

The Graph subgraphs are **deliberately excluded** from this module's hot path. They are useful for cross-validation later but introduce a third-party dependency for data that is reconstructible from sources 3.1–3.3 alone.

## 4. Data Model

The local store consists of five tables (or Parquet files, depending on storage backend choice — see §6).

### 4.1 `markets`

| Column | Type | Notes |
|---|---|---|
| `condition_id` | string (0x...) | Primary key. The CTF condition identifier. |
| `question` | string | Market question text. |
| `category` | string | Coarse category: politics, crypto, sports, macro, weather, other. Derived. |
| `subcategory` | string \| null | Finer-grained tag if available. |
| `created_ts` | int64 (unix sec) | Market creation timestamp. |
| `end_ts` | int64 | Scheduled end. |
| `resolution_ts` | int64 | Actual resolution timestamp. |
| `resolution_outcome` | int (0/1) | Binary resolution. Multi-outcome markets are decomposed into binary sub-markets per outcome. |
| `contract_version` | int (1 or 2) | Which CTF Exchange contract this market traded on. |
| `migration_split` | bool | True if market was active across the April 28 boundary. |
| `unique_traders` | int | Computed at ingestion time; used for downstream filtering. |
| `total_volume_usd` | float | Computed. |

### 4.2 `trades`

| Column | Type | Notes |
|---|---|---|
| `trade_id` | string | Synthetic: `{tx_hash}:{log_index}`. Primary key. |
| `condition_id` | string | Foreign key to `markets`. |
| `block_ts` | int64 | Block timestamp of the trade. |
| `taker_wallet` | string (0x...) | The wallet whose order crossed. |
| `maker_wallet` | string (0x...) | The resting order's wallet. |
| `side` | enum (YES_BUY, YES_SELL, NO_BUY, NO_SELL) | Normalized direction. |
| `price` | float | Execution price in [0, 1]. |
| `size_shares` | float | Position size in CTF shares. |
| `size_usd` | float | `price * size_shares`. |
| `tx_hash` | string | For audit / re-derivation. |
| `source` | enum (DATA_API, ONCHAIN) | Which source produced this row. Both populated when cross-validated. |

### 4.3 `wallet_trades_index`

A redundant denormalization for fast per-wallet queries. Same schema as `trades` but partitioned by wallet rather than by market. Populated as a derived view.

### 4.4 `market_prices`

| Column | Type | Notes |
|---|---|---|
| `condition_id` | string | FK. |
| `ts` | int64 | Timestamp bucket. |
| `mid_price` | float | Mid of best bid / best ask in [0, 1]. |
| `bid` | float | Best bid. |
| `ask` | float | Best ask. |
| `source` | enum (TRADE_INFERRED, ORDERBOOK) | How this price was derived. |

Mid-price reconstruction strategy:
- **Primary**: For each trade, the trade price itself is a noisy estimate of mid. Aggregate at minute resolution by VWAP over the bucket.
- **Secondary**: If a market has periods with no trades, linearly interpolate between adjacent VWAP points and mark `source=TRADE_INFERRED`.
- **Tertiary (deferred)**: Orderbook subgraph queries can populate true mid-price at higher resolution if needed for specific high-precision markets. Not part of the v1 ingestion.

This is a documented approximation. The bias is that trade prices reflect spread-crossing and so slightly overestimate volatility versus the true mid. Downstream BSS computation must be aware that price-at-entry is a VWAP-bucketed estimate, not a tick-exact value.

### 4.5 `ingestion_log`

| Column | Type | Notes |
|---|---|---|
| `run_id` | string (uuid) | One row per ingestion run. |
| `start_ts` | int64 | When the run started. |
| `end_ts` | int64 | When it finished. |
| `markets_added` | int | |
| `trades_added` | int | |
| `wallets_seen` | int | |
| `errors` | json | List of error records. |
| `git_commit` | string | Code version that produced this run. |

This table is the audit trail. Every downstream artifact should be traceable to a run_id.

## 5. Ingestion Workflow

The workflow runs in five stages, in order. Each stage is idempotent: re-running it should produce the same result and skip work already done.

### Stage A — Market Discovery
1. Query Gamma API for all markets with `closed=true`, paginating to completion.
2. For each, determine `contract_version` from the resolution_ts (before/after the migration boundary) and from explicit contract address fields where present.
3. Upsert into `markets`. Use Gamma's condition_id as primary key for natural deduplication on re-run.
4. Derive `category` via rule-based tagging on the question text (regex / keyword matching against a curated taxonomy). This is intentionally low-tech; refinement is a later concern.

### Stage B — Trade Ingestion (Data API path)
For each market in `markets` not yet fully ingested:
1. Page through `data-api.polymarket.com/trades?market={condition_id}` with cursor pagination.
2. For each trade record: normalize side, compute `size_usd`, generate synthetic `trade_id`, upsert into `trades` with `source=DATA_API`.
3. Termination: empty response page → mark market as fully ingested in `ingestion_log`.
4. Rate limiting: token bucket at 2 req/sec with exponential backoff on 429.

### Stage C — On-Chain Cross-Validation (selective)
For a sampled subset of markets (e.g., 5% random sample plus all "high-value" markets above a volume threshold):
1. Pull the relevant range of Polygon blocks.
2. Filter logs by CTF Exchange V1 or V2 contract address (whichever applies to that market).
3. Decode `OrderFilled` events into the same trade schema.
4. Reconcile against Data API rows: match on `tx_hash + log_index`. Any discrepancies are recorded as errors in `ingestion_log` for manual review.

This stage exists because the Data API is a closed-source service that has been known to omit edge cases (failed fills, partial cancellations counted as fills, V1/V2 boundary trades). On-chain is the ground truth. Cross-validating a sample tells us whether to trust the Data API for the bulk of ingestion.

### Stage D — Wallet Index Build
After Stages A–C complete:
1. Scan `trades`, partition by `taker_wallet` and `maker_wallet`.
2. Write `wallet_trades_index` partitioned by wallet, with each row tagged whether the wallet was taker or maker on that trade.
3. Compute per-wallet aggregate stats (trade count, unique markets touched, total volume) and emit as a separate `wallet_summary` artifact for downstream filtering.

### Stage E — Price Series Generation
1. For each market, bucket trades by minute.
2. Compute VWAP per bucket → `mid_price`.
3. Forward-fill gaps with linear interpolation, capped at a maximum gap length (e.g., 6 hours) beyond which the bucket is left null.
4. Write to `market_prices`.

## 6. Storage Backend

**Choice: Parquet files + DuckDB query layer.**

Rationale:
- **Parquet** is columnar, compressed, language-agnostic, and natively partitioned. It makes the dataset trivially shareable (copy a directory) and survives any tooling change.
- **DuckDB** queries Parquet directly with full SQL, no server, no schema migrations. It handles the research-scale data volumes here (estimated 10–100 GB total) entirely in-process and out-of-core.
- This combination skips the operational overhead of TimescaleDB or ClickHouse for a workload that is fundamentally batch read-heavy after ingestion.

Layout:
```
data/
  markets/
    markets.parquet
  trades/
    contract_version=1/year=2025/month=03/trades.parquet
    contract_version=2/year=2026/month=05/trades.parquet
    ...
  wallet_index/
    wallet_prefix=00/wallets.parquet
    wallet_prefix=01/wallets.parquet
    ...
  market_prices/
    condition_id={hash_prefix}/prices.parquet
  ingestion_log/
    ingestion_log.parquet
```

Wallet partitioning by address prefix (first 2 hex chars → 256 partitions) gives roughly balanced shards and supports fast per-wallet lookups without indexing infrastructure.

## 7. Error Handling & Idempotency

Every stage must satisfy three properties:

1. **Crash safety**: Killing the process mid-run leaves the on-disk state consistent. Achieved via write-to-temp-then-rename for all Parquet writes, and by treating `ingestion_log` as the source of truth for "what has been completed."

2. **Idempotent re-run**: Running the same stage twice with no new data must be a no-op. Achieved via primary-key upserts on `markets` and `trades`, and via completion checkpoints in `ingestion_log`.

3. **Resumability**: A re-run after a partial failure must pick up where it left off, not redo completed work. Achieved by checkpointing per-market in Stage B and per-block-range in Stage C.

Error categories and handling:
- **Transient HTTP errors (5xx, network timeouts)**: retry with exponential backoff up to 5 attempts, then log and continue.
- **Rate limits (429)**: respect `Retry-After` header if present, else back off and retry.
- **Schema mismatches** (e.g., API returns a field with an unexpected type): hard fail the run with full record dumped to `ingestion_log.errors`. The schema is fragile by design — silent coercion of unexpected data would corrupt downstream analysis.
- **Reconciliation discrepancies** in Stage C: log but do not fail. Treat as a data quality signal to be triaged.

## 8. The Migration Boundary

The April 28, 2026 V1 → V2 cutover is the single most fragile point in the pipeline. Handling:

1. **Markets straddling the boundary**: a market created pre-cutover and resolved post-cutover has trades on both contract versions. `markets.migration_split = true` flags these. They are *not* treated as two separate markets — they are one market with trades from two sources.

2. **Side and price normalization**: V1 and V2 have different order struct fields. The normalization to the canonical `side` enum and `price` float must be done at parse time, with the contract version determining which decoder is used. A unit test should verify that a V1 trade and a V2 trade of identical economic content produce identical normalized rows.

3. **Wallet identity persistence**: wallets are the same addresses across versions. No mapping needed.

4. **pUSD vs USDC.e**: collateral token differs across versions but is 1:1 USDC-backed in both cases. All USD values in the dataset are direct USDC equivalents; the underlying token is recorded only in the raw on-chain audit trail.

## 9. Testing & Validation

Three layers of testing:

**Unit tests**: every parser, normalizer, and price-bucketing function has unit tests with fixture inputs (real captured API responses and on-chain logs, anonymized). These run on every commit.

**Integration tests**: a small fixed market (one resolved, low-volume market with ~50 trades) is ingested end-to-end on every CI run, with assertions on the resulting row counts, wallet counts, and price series.

**Validation queries**: a notebook of validation SQL queries is run after every full ingestion. Examples:
- Total trade volume per market vs. Gamma API's reported volume (should match within 1%).
- Number of unique wallets per market, distribution check.
- Time gaps in `market_prices` (no gap should exceed the configured maximum).
- Cross-validation discrepancy rate from Stage C (should be <0.5%).

A run is considered successful only if validation queries pass.

## 10. Configuration

All operational parameters in a single YAML config file:

```yaml
ingestion:
  data_api_base: https://data-api.polymarket.com
  gamma_api_base: https://gamma-api.polymarket.com
  polygon_rpc: <env var>
  rate_limit_rps: 2.0
  retry_max_attempts: 5
  retry_backoff_base_sec: 2.0
  cross_validation_sample_rate: 0.05
  cross_validation_min_volume_usd: 100000

storage:
  root_path: ./data
  parquet_compression: zstd
  wallet_partition_bits: 8

price_series:
  bucket_seconds: 60
  max_interpolation_gap_seconds: 21600

migration:
  v1_v2_cutover_ts: 1745841600  # 2026-04-28 12:00 UTC
  v1_exchange_addresses: [...]
  v2_exchange_addresses: [...]
```

## 11. Out-of-Band Operational Concerns

- **Initial backfill cost**: estimated 24–72 hours of wall-clock time for the full historical Polymarket dataset on a free Polygon RPC, dominated by Stage B rate limits and Stage C block scanning. Subsequent incremental runs are minutes.
- **Disk footprint**: estimated 30–80 GB compressed Parquet for the full dataset including derived indexes.
- **Memory**: DuckDB can stream-process the full dataset on a laptop. The HPC cluster is overkill for ingestion but appropriate for downstream modeling.
- **Polymarket API changes**: the Data API is undocumented in places and has changed schemas before. The hard-fail-on-schema-mismatch policy in §7 is the explicit defense against silent breakage.

## 12. Deliverables of This Module

When complete, this module produces:
1. A populated `./data/` directory containing all five tables in Parquet format.
2. A reproducible config file documenting the exact run parameters.
3. A validation report from the post-ingestion notebook.
4. An `ingestion_log` that allows any downstream analysis to be tied back to specific source data versions.

Downstream modules (wallet profiling, correlation analysis, aggregation, backtesting) consume *only* the Parquet outputs and should never make their own API calls. This separation is what makes the rest of the project reproducible.