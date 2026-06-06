"""Stage E — Price Series Generation.

For each market, buckets trades by minute, computes VWAP per bucket,
forward-fills gaps with linear interpolation (up to max_interpolation_gap_seconds),
and writes to market_prices.
"""

from __future__ import annotations

import logging
import math
from collections import defaultdict
from typing import Any, Dict, List, Optional

import duckdb
import pyarrow as pa

from ..config import Config
from ..models import MarketPrice, PriceSource
from ..storage import Storage

log = logging.getLogger(__name__)


def _vwap(prices: List[float], sizes: List[float]) -> float:
    total_size = sum(sizes)
    if total_size == 0:
        return 0.5
    return sum(p * s for p, s in zip(prices, sizes)) / total_size


def _interpolate(
    buckets: Dict[int, Dict[str, Any]],
    bucket_seconds: int,
    max_gap: int,
) -> List[MarketPrice]:
    """Linear interpolation of empty buckets between known VWAP points."""
    if not buckets:
        return []

    sorted_ts = sorted(buckets.keys())
    result: List[MarketPrice] = []

    # Emit known buckets
    for ts, b in sorted(buckets.items()):
        result.append(
            MarketPrice(
                condition_id=b["condition_id"],
                ts=ts,
                mid_price=b["vwap"],
                bid=b["vwap"],
                ask=b["vwap"],
                source=PriceSource.TRADE_INFERRED,
            )
        )

    # Fill gaps
    filled: List[MarketPrice] = []
    result.sort(key=lambda x: x.ts)

    for i in range(len(result) - 1):
        filled.append(result[i])
        a = result[i]
        b = result[i + 1]
        gap = b.ts - a.ts
        if gap > bucket_seconds and gap <= max_gap:
            steps = gap // bucket_seconds - 1
            for k in range(1, steps + 1):
                frac = k / (steps + 1)
                interp_ts = a.ts + k * bucket_seconds
                interp_price = a.mid_price + frac * (b.mid_price - a.mid_price)
                filled.append(
                    MarketPrice(
                        condition_id=a.condition_id,
                        ts=interp_ts,
                        mid_price=interp_price,
                        bid=interp_price,
                        ask=interp_price,
                        source=PriceSource.TRADE_INFERRED,
                    )
                )

    if result:
        filled.append(result[-1])

    return filled


def run(cfg: Config, storage: Storage) -> int:
    """Run Stage E. Returns total price rows written."""
    trades_root = storage.root / "trades"
    if not trades_root.exists():
        return 0

    con = duckdb.connect()
    try:
        all_trades = con.execute(
            f"SELECT condition_id, block_ts, price, size_shares"
            f" FROM read_parquet('{trades_root}/**/*.parquet', hive_partitioning=false)"
        ).fetch_arrow_table()
    except Exception as exc:
        log.error("Stage E: failed to read trades: %s", exc)
        return 0

    bucket_sec = cfg.price_series.bucket_seconds
    max_gap = cfg.price_series.max_interpolation_gap_seconds

    # Group by market, then bucket
    by_market: Dict[str, Dict[int, Dict[str, Any]]] = defaultdict(dict)

    for row in all_trades.to_pylist():
        bucket_ts = (int(row["block_ts"]) // bucket_sec) * bucket_sec
        market_buckets = by_market[row["condition_id"]]
        if bucket_ts not in market_buckets:
            market_buckets[bucket_ts] = {
                "condition_id": row["condition_id"],
                "prices": [],
                "sizes": [],
                "vwap": 0.0,
            }
        market_buckets[bucket_ts]["prices"].append(float(row["price"]))
        market_buckets[bucket_ts]["sizes"].append(float(row["size_shares"]))

    # Compute VWAP per bucket
    for condition_id, buckets in by_market.items():
        for ts, b in buckets.items():
            b["vwap"] = _vwap(b["prices"], b["sizes"])

    # Collect all prices first, then write once per file to avoid
    # repeated read-merge-write cycles that cause schema mismatches.
    all_prices: List[MarketPrice] = []
    total = 0
    for condition_id, buckets in by_market.items():
        prices = _interpolate(buckets, bucket_sec, max_gap)
        all_prices.extend(prices)
        total += len(prices)
        log.info("market %s: %d price rows", condition_id, len(prices))

    storage.write_prices(all_prices)

    return total
