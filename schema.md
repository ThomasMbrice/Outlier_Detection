# Schema Reference

All tables are stored as Parquet files under `./data/`. DuckDB is the query layer — no server required.

---

## Table of Contents

1. [markets](#1-markets)
2. [trades](#2-trades)
3. [wallet_index](#3-wallet_index)
4. [market_prices](#4-market_prices)
5. [ingestion_log](#5-ingestion_log)
6. [Enumerations](#6-enumerations)
7. [Storage Layout](#7-storage-layout)
8. [Known Limitations](#8-known-limitations)

---

## 1. `markets`

**Path:** `data/markets/markets.parquet`

One row per resolved binary market. Unresolved markets are never written.

| Column | Type | Nullable | Description |
|---|---|---|---|
| `condition_id` | string | No | Primary key. CTF condition identifier (`0x...`). |
| `question` | string | No | Market question text. |
| `category` | string | No | Coarse category. See [Enumerations](#6-enumerations). |
| `subcategory` | string | Yes | Finer-grained tag if available; otherwise null. |
| `created_ts` | int64 | No | Market creation timestamp (Unix seconds). |
| `end_ts` | int64 | No | Scheduled close timestamp (Unix seconds). |
| `resolution_ts` | int64 | No | Actual resolution timestamp (Unix seconds). Derived from `closedTime` in Gamma API, falling back to `endDate`. |
| `resolution_outcome` | int8 | No | Binary outcome: `1` = YES resolved, `0` = NO resolved. Derived from `outcomePrices[0]` (≥ 0.99 → 1, ≤ 0.01 → 0). |
| `contract_version` | int8 | No | CTF Exchange contract version: `1` (pre-2026-04-28) or `2` (post). |
| `migration_split` | bool | No | `true` if the market was active across the V1→V2 cutover (2026-04-28 12:00 UTC). |
| `unique_traders` | int32 | No | Unique trader count. Currently `0` — not provided by Gamma API; populated post-ingestion by downstream profiling. |
| `total_volume_usd` | float64 | No | Lifetime USD volume as reported by Gamma API (`volumeNum`). |

**Primary key:** `condition_id`

**Source:** Gamma API (`gamma-api.polymarket.com/markets`)

---

## 2. `trades`

**Path:** `data/trades/contract_version={1|2}/year={YYYY}/month={MM}/trades.parquet`

One row per trade fill. Partitioned by contract version, year, and month of `block_ts`.

| Column | Type | Nullable | Description |
|---|---|---|---|
| `trade_id` | string | No | Primary key. Synthetic: `{tx_hash}:{outcome_index}`. |
| `condition_id` | string | No | Foreign key → `markets.condition_id`. |
| `block_ts` | int64 | No | Block timestamp of the trade (Unix seconds). |
| `taker_wallet` | string | No | Trader's proxy wallet address (lowercase `0x...`). Sourced from `proxyWallet` in Data API. |
| `maker_wallet` | string | No | Resting order wallet. Empty string when sourced from Data API (not exposed); populated only for `ONCHAIN` source rows. |
| `side` | string | No | Normalized direction. See [Enumerations](#6-enumerations). |
| `price` | float64 | No | Execution price in \[0, 1\]. |
| `size_shares` | float64 | No | Position size in CTF shares. |
| `size_usd` | float64 | No | `price × size_shares`. |
| `tx_hash` | string | No | Transaction hash (`0x...`). Audit / re-derivation only. |
| `source` | string | No | Which source produced this row. See [Enumerations](#6-enumerations). |

**Primary key:** `trade_id`

**Foreign key:** `condition_id` → `markets.condition_id`

**Source:** Data API (`data-api.polymarket.com/trades`); on-chain fallback via Polygon RPC (Stage C).

**Notes:**
- `outcome_index` in `trade_id` is `0` for YES-token trades and `1` for NO-token trades.
- `maker_wallet` is blank for all `DATA_API` rows. Only on-chain decoded rows (Stage C) carry both wallets.
- `side` is inferred from `outcomeIndex` (0 = YES, 1 = NO) combined with `side` field (`BUY`/`SELL`).

---

## 3. `wallet_index`

**Path:** `data/wallet_index/wallet_prefix={xx}/wallets.parquet`

Denormalized per-wallet view of `trades`. 256 partitions by the first two hex characters of the wallet address (after `0x`). Same columns as `trades` plus two additional:

| Column | Type | Description |
|---|---|---|
| *(all trades columns)* | — | Identical to `trades` schema above. |
| `wallet` | string | The wallet address this row is indexed under. |
| `role` | string | `"taker"` or `"maker"`. |

**Also written:** `data/wallet_index/wallet_summary.parquet`

| Column | Type | Description |
|---|---|---|
| `wallet` | string | Wallet address. |
| `trade_count` | int32 | Total trades (taker + maker). |
| `unique_markets` | int32 | Number of distinct markets touched. |
| `total_volume_usd` | float64 | Sum of `size_usd` across all trades. |

**Source:** Derived from `trades` in Stage D.

---

## 4. `market_prices`

**Path:** `data/market_prices/condition_id={prefix}/prices.parquet`

VWAP-bucketed mid-price time series per market. Partitioned by the first 4 characters of `condition_id`.

| Column | Type | Nullable | Description |
|---|---|---|---|
| `condition_id` | string | No | Foreign key → `markets.condition_id`. |
| `ts` | int64 | No | Bucket start timestamp (Unix seconds). Bucket width = `price_series.bucket_seconds` (default: 60s). |
| `mid_price` | float64 | No | VWAP over the bucket, or linearly interpolated. In \[0, 1\]. |
| `bid` | float64 | No | Equal to `mid_price` for trade-inferred rows. |
| `ask` | float64 | No | Equal to `mid_price` for trade-inferred rows. |
| `source` | string | No | How the price was derived. See [Enumerations](#6-enumerations). |

**Foreign key:** `condition_id` → `markets.condition_id`

**Source:** Derived from `trades` in Stage E.

**Approximation notes:**
- Trade prices are spread-crossing prices, so they slightly overestimate true mid volatility.
- Gaps ≤ `price_series.max_interpolation_gap_seconds` (default: 6 hours) are linearly interpolated and marked `TRADE_INFERRED`. Gaps beyond this threshold are left null (bucket absent).
- `bid` and `ask` are identical to `mid_price` for all `TRADE_INFERRED` rows. True spread requires orderbook data (out of scope for v1).

---

## 5. `ingestion_log`

**Path:** `data/ingestion_log/ingestion_log.parquet`

Append-only audit trail. One row per pipeline run.

| Column | Type | Nullable | Description |
|---|---|---|---|
| `run_id` | string | No | UUID identifying this run. |
| `start_ts` | int64 | No | Run start (Unix seconds). |
| `end_ts` | int64 | No | Run end (Unix seconds). |
| `markets_added` | int32 | No | Markets upserted in Stage A. |
| `trades_added` | int32 | No | Trades upserted in Stage B. |
| `wallets_seen` | int32 | No | Unique wallets indexed in Stage D. |
| `errors` | string | No | JSON array of error records. Empty array `[]` on clean run. Each record has `stage` (A–E) and `error` (message string). |
| `git_commit` | string | No | SHA of the code version that produced this run. `"unknown"` if not in a git repo. |

**Every downstream artifact should be traceable to a `run_id`.**

---

## 6. Enumerations

### `category` (markets)

Derived by regex matching on `question` text. Rules are in `src/stages/stage_a_discovery.py`.

| Value | Description |
|---|---|
| `politics` | Elections, legislation, government figures |
| `crypto` | BTC, ETH, DeFi, NFTs, token prices |
| `sports` | NFL, NBA, MLB, NHL, soccer, tennis, golf |
| `macro` | Fed policy, inflation, GDP, recession |
| `weather` | Hurricanes, earthquakes, storms |
| `other` | Anything not matched above |

### `side` (trades)

| Value | Description |
|---|---|
| `YES_BUY` | Taker bought YES outcome token |
| `YES_SELL` | Taker sold YES outcome token |
| `NO_BUY` | Taker bought NO outcome token |
| `NO_SELL` | Taker sold NO outcome token |

Derived from `outcomeIndex` (0 = YES, 1 = NO) × `side` field (`BUY`/`SELL`) from the Data API.

### `source` (trades)

| Value | Description |
|---|---|
| `DATA_API` | Row produced by Data API (Stage B) |
| `ONCHAIN` | Row decoded from Polygon chain logs (Stage C) |

### `source` (market_prices)

| Value | Description |
|---|---|
| `TRADE_INFERRED` | VWAP of trades in bucket, or linear interpolation of adjacent VWAPs |
| `ORDERBOOK` | True mid from orderbook snapshot (not implemented in v1) |

---

## 7. Storage Layout

```
data/
  markets/
    markets.parquet
  trades/
    contract_version=1/
      year=2020/month=10/trades.parquet
      ...
    contract_version=2/
      year=2026/month=04/trades.parquet
      ...
  wallet_index/
    wallet_prefix=00/wallets.parquet
    wallet_prefix=01/wallets.parquet
    ...                              (256 shards, 00–ff)
    wallet_summary.parquet
  market_prices/
    condition_id=0x15/prices.parquet
    condition_id=0x49/prices.parquet
    ...
  ingestion_log/
    ingestion_log.parquet
```

**Compression:** `zstd` (configurable via `storage.parquet_compression`).

**Query example (DuckDB):**

```sql
-- All trades for a specific wallet
SELECT * FROM read_parquet('data/wallet_index/wallet_prefix=ab/wallets.parquet')
WHERE wallet = '0xab1234...';

-- Price series for a market
SELECT ts, mid_price FROM read_parquet('data/market_prices/**/*.parquet')
WHERE condition_id = '0x...'
ORDER BY ts;

-- Markets by category
SELECT category, count(*), sum(total_volume_usd)
FROM read_parquet('data/markets/markets.parquet')
GROUP BY category ORDER BY 2 DESC;
```

---

## 8. Known Limitations

| Limitation | Detail |
|---|---|
| `maker_wallet` blank for Data API trades | The `/trades` endpoint only returns `proxyWallet` (the taker). Maker wallet is only available via on-chain decoding (Stage C). |
| `unique_traders` always 0 | Not returned by Gamma API. Populated by downstream profiling layer, not ingestion. |
| `bid`/`ask` equal `mid_price` | True spread requires orderbook data; deferred to v2. |
| Price series gaps > 6h are null | Buckets with no trades and no adjacent anchor within 6 hours are absent from `market_prices`. |
| Data API pagination cap | Gamma API hard-caps offset at 10,000 (100 pages × 100 results). Markets beyond this offset are not ingested. |
| `resolution_outcome` inferred | Derived from `outcomePrices[0]` threshold (≥ 0.99 / ≤ 0.01). Markets with ambiguous final prices are skipped. |
