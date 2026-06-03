"""Parquet + DuckDB storage layer.

All writes go to a temp file first, then are atomically renamed into place
(crash safety). Upserts are implemented as read-merge-write sequences so
that re-running a stage is idempotent.
"""

from __future__ import annotations

import json
import tempfile
import uuid
from dataclasses import asdict
from pathlib import Path
from typing import Any, Dict, Iterable, List

import duckdb
import pyarrow as pa
import pyarrow.parquet as pq

from .config import StorageConfig
from .models import (
    IngestionRun,
    Market,
    MarketPrice,
    Trade,
    INGESTION_LOG_SCHEMA,
    MARKETS_SCHEMA,
    MARKET_PRICES_SCHEMA,
    TRADES_SCHEMA,
)


class Storage:
    def __init__(self, cfg: StorageConfig) -> None:
        self.cfg = cfg
        self.root = cfg.root_path.resolve()  # always absolute so DuckDB paths work
        self._ensure_dirs()

    # ------------------------------------------------------------------
    # Directory helpers
    # ------------------------------------------------------------------

    def _ensure_dirs(self) -> None:
        for sub in ("markets", "trades", "wallet_index", "market_prices", "ingestion_log"):
            (self.root / sub).mkdir(parents=True, exist_ok=True)

    def _markets_path(self) -> Path:
        return self.root / "markets" / "markets.parquet"

    def _trades_path(self, contract_version: int, year: int, month: int) -> Path:
        d = self.root / "trades" / f"contract_version={contract_version}" / f"year={year}" / f"month={month:02d}"
        d.mkdir(parents=True, exist_ok=True)
        return d / "trades.parquet"

    def _wallet_index_path(self, prefix: str) -> Path:
        d = self.root / "wallet_index" / f"wallet_prefix={prefix}"
        d.mkdir(parents=True, exist_ok=True)
        return d / "wallets.parquet"

    def _prices_path(self, condition_id: str) -> Path:
        prefix = condition_id[:4] if len(condition_id) >= 4 else condition_id
        d = self.root / "market_prices" / f"condition_id={prefix}"
        d.mkdir(parents=True, exist_ok=True)
        return d / "prices.parquet"

    def _ingestion_log_path(self) -> Path:
        return self.root / "ingestion_log" / "ingestion_log.parquet"

    # ------------------------------------------------------------------
    # Atomic write helper
    # ------------------------------------------------------------------

    def _atomic_write(self, path: Path, table: pa.Table) -> None:
        tmp = path.with_suffix(f".{uuid.uuid4().hex}.tmp")
        pq.write_table(table, tmp, compression=self.cfg.parquet_compression)
        tmp.rename(path)

    # ------------------------------------------------------------------
    # Markets
    # ------------------------------------------------------------------

    def upsert_markets(self, markets: Iterable[Market]) -> int:
        new_rows = [_market_to_dict(m) for m in markets]
        if not new_rows:
            return 0

        new_table = pa.Table.from_pylist(new_rows)
        path = self._markets_path()

        if path.exists():
            existing = pq.read_table(path)
            combined = _dedup_by_key(existing, new_table, "condition_id")
        else:
            combined = new_table

        self._atomic_write(path, combined)
        return len(new_rows)

    def list_market_ids(self) -> List[str]:
        path = self._markets_path()
        if not path.exists():
            return []
        tbl = pq.read_table(path, columns=["condition_id"])
        return tbl.column("condition_id").to_pylist()

    # ------------------------------------------------------------------
    # Trades
    # ------------------------------------------------------------------

    def upsert_trades(self, trades: Iterable[Trade]) -> int:
        from datetime import datetime, timezone

        by_partition: Dict[tuple, List[Dict]] = {}
        for t in trades:
            dt = datetime.fromtimestamp(t.block_ts, tz=timezone.utc)
            key = (t.condition_id, dt.year, dt.month)  # we determine contract_version below
            by_partition.setdefault(key, []).append(_trade_to_dict(t))

        total = 0
        for (condition_id, year, month), rows in by_partition.items():
            # Determine contract version from the first row's trade_id lookup would
            # require joining markets; instead we look it up via DuckDB if markets exist.
            cv = self._get_contract_version(condition_id)
            path = self._trades_path(cv, year, month)
            new_table = pa.Table.from_pylist(rows)

            if path.exists():
                # Read only core columns — strips any hive partition cols PyArrow may add
                existing = pq.read_table(path, columns=list(new_table.schema.names))
                combined = _dedup_by_key(existing, new_table, "trade_id")
            else:
                combined = new_table

            self._atomic_write(path, combined)
            total += len(rows)

        return total

    def _get_contract_version(self, condition_id: str) -> int:
        mp = self._markets_path()
        if not mp.exists():
            return 2
        con = duckdb.connect()
        result = con.execute(
            "SELECT contract_version FROM read_parquet(?) WHERE condition_id = ? LIMIT 1",
            [str(mp), condition_id],
        ).fetchone()
        return int(result[0]) if result else 2

    # ------------------------------------------------------------------
    # Wallet index
    # ------------------------------------------------------------------

    def write_wallet_index(self, trades: Iterable[Trade]) -> None:
        by_prefix: Dict[str, List[Dict]] = {}
        for t in trades:
            d = _trade_to_dict(t)
            for wallet, role in [(t.taker_wallet, "taker"), (t.maker_wallet, "maker")]:
                prefix = wallet[2:4].lower() if wallet.startswith("0x") else wallet[:2].lower()
                row = {**d, "wallet": wallet, "role": role}
                by_prefix.setdefault(prefix, []).append(row)

        for prefix, rows in by_prefix.items():
            path = self._wallet_index_path(prefix)
            new_table = pa.Table.from_pylist(rows)
            if path.exists():
                existing = pq.read_table(path)
                combined = pa.concat_tables([existing, new_table])
            else:
                combined = new_table
            self._atomic_write(path, combined)

    # ------------------------------------------------------------------
    # Market prices
    # ------------------------------------------------------------------

    def write_prices(self, prices: Iterable[MarketPrice]) -> None:
        by_market: Dict[str, List[Dict]] = {}
        for p in prices:
            by_market.setdefault(p.condition_id, []).append(asdict(p))

        for condition_id, rows in by_market.items():
            path = self._prices_path(condition_id)
            new_table = pa.Table.from_pylist(rows)
            if path.exists():
                existing = pq.read_table(path)
                combined = _dedup_by_key(existing, new_table, key=None, sort_by="ts")
            else:
                combined = new_table
            self._atomic_write(path, combined)

    # ------------------------------------------------------------------
    # Ingestion log
    # ------------------------------------------------------------------

    def append_run(self, run: IngestionRun) -> None:
        row = {
            "run_id": run.run_id,
            "start_ts": run.start_ts,
            "end_ts": run.end_ts,
            "markets_added": run.markets_added,
            "trades_added": run.trades_added,
            "wallets_seen": run.wallets_seen,
            "errors": json.dumps(run.errors),
            "git_commit": run.git_commit,
        }
        path = self._ingestion_log_path()
        new_table = pa.table({k: [v] for k, v in row.items()})
        if path.exists():
            existing = pq.read_table(path)
            combined = pa.concat_tables([existing, new_table])
        else:
            combined = new_table
        self._atomic_write(path, combined)

    # ------------------------------------------------------------------
    # Query helper (DuckDB)
    # ------------------------------------------------------------------

    def query(self, sql: str) -> duckdb.DuckDBPyRelation:
        con = duckdb.connect()
        # Make data root available as a macro so SQL can reference it
        root = str(self.root)
        con.execute(f"SET search_path = '{root}'")
        return con.execute(sql)


# ------------------------------------------------------------------
# Private helpers
# ------------------------------------------------------------------

def _dedup_by_key(
    existing: pa.Table,
    incoming: pa.Table,
    key: str | None,
    sort_by: str | None = None,
) -> pa.Table:
    """Merge two tables, keeping incoming rows when keys collide."""
    if key is None:
        combined = pa.concat_tables([existing, incoming])
    else:
        existing_ids = set(existing.column(key).to_pylist())
        mask = pa.array([k not in existing_ids for k in incoming.column(key).to_pylist()])
        new_only = incoming.filter(mask)
        combined = pa.concat_tables([existing, new_only])

    if sort_by and sort_by in combined.schema.names:
        indices = pa.compute.sort_indices(combined, sort_keys=[(sort_by, "ascending")])
        combined = combined.take(indices)

    return combined


def _market_to_dict(m: Market) -> Dict[str, Any]:
    return {
        "condition_id": m.condition_id,
        "question": m.question,
        "category": m.category,
        "subcategory": m.subcategory,
        "created_ts": m.created_ts,
        "end_ts": m.end_ts,
        "resolution_ts": m.resolution_ts,
        "resolution_outcome": m.resolution_outcome,
        "contract_version": m.contract_version,
        "migration_split": m.migration_split,
        "unique_traders": m.unique_traders,
        "total_volume_usd": m.total_volume_usd,
    }


def _trade_to_dict(t: Trade) -> Dict[str, Any]:
    return {
        "trade_id": t.trade_id,
        "condition_id": t.condition_id,
        "block_ts": t.block_ts,
        "taker_wallet": t.taker_wallet,
        "maker_wallet": t.maker_wallet,
        "side": t.side.value,
        "price": t.price,
        "size_shares": t.size_shares,
        "size_usd": t.size_usd,
        "tx_hash": t.tx_hash,
        "source": t.source.value,
    }
