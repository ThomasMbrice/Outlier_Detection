"""Stage D — Wallet Index Build.

Scans all trades, partitions by wallet prefix, and writes the wallet_index.
Also computes per-wallet aggregate stats into a wallet_summary artifact.
"""

from __future__ import annotations

import logging
from collections import defaultdict
from pathlib import Path
from typing import Any, Dict, List

import duckdb
import pyarrow as pa
import pyarrow.parquet as pq

from ..config import Config
from ..models import Trade, TradeSource, Side
from ..storage import Storage, _trade_to_dict

log = logging.getLogger(__name__)


def run(cfg: Config, storage: Storage) -> int:
    """Run Stage D. Returns number of unique wallets indexed."""
    trades_root = storage.root / "trades"
    if not trades_root.exists():
        log.warning("Stage D: no trades directory found")
        return 0

    _TRADE_COLS = ["trade_id", "condition_id", "block_ts", "taker_wallet", "maker_wallet",
                   "side", "price", "size_shares", "size_usd", "tx_hash", "source"]

    con = duckdb.connect()
    try:
        cols = ", ".join(_TRADE_COLS)
        all_trades = con.execute(
            f"SELECT {cols} FROM read_parquet('{trades_root}/**/*.parquet', hive_partitioning=false)"
        ).fetch_arrow_table()
    except Exception as exc:
        log.error("Stage D: failed to read trades: %s", exc)
        return 0

    wallets_seen = set()
    by_prefix: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    wallet_stats: Dict[str, Dict[str, Any]] = defaultdict(lambda: {
        "trade_count": 0,
        "markets": set(),
        "total_volume_usd": 0.0,
    })

    for row in all_trades.to_pylist():
        for wallet, role in [(row["taker_wallet"], "taker"), (row["maker_wallet"], "maker")]:
            if not wallet:
                continue
            wallets_seen.add(wallet)
            prefix = wallet[2:4].lower() if wallet.startswith("0x") else wallet[:2].lower()
            by_prefix[prefix].append({**row, "wallet": wallet, "role": role})

            stats = wallet_stats[wallet]
            stats["trade_count"] += 1
            stats["markets"].add(row["condition_id"])
            stats["total_volume_usd"] += float(row["size_usd"] or 0)

    # Write partitioned wallet index
    for prefix, rows in by_prefix.items():
        path = storage._wallet_index_path(prefix)
        storage._atomic_write(path, pa.Table.from_pylist(rows))

    # Write wallet summary
    summary_rows = [
        {
            "wallet": w,
            "trade_count": s["trade_count"],
            "unique_markets": len(s["markets"]),
            "total_volume_usd": s["total_volume_usd"],
        }
        for w, s in wallet_stats.items()
    ]
    if summary_rows:
        summary_path = storage.root / "wallet_index" / "wallet_summary.parquet"
        storage._atomic_write(summary_path, pa.Table.from_pylist(summary_rows))

    log.info("Stage D complete: %d unique wallets indexed", len(wallets_seen))
    return len(wallets_seen)
