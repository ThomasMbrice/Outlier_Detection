"""Stage C — On-Chain Cross-Validation.

Pulls a sampled subset of markets from the Polygon chain, decodes OrderFilled
events, and reconciles against Data API rows. Discrepancies are logged but
do not fail the run.
"""

from __future__ import annotations

import logging
import random
from typing import Any, Dict, List

import pyarrow.parquet as pq

from ..clients.polygon_rpc import PolygonRPCClient
from ..config import Config
from ..decoders import v1_decoder, v2_decoder
from ..models import Trade, TradeSource
from ..storage import Storage

log = logging.getLogger(__name__)


def _sample_markets(market_ids: List[str], sample_rate: float, min_volume: float, storage: Storage) -> List[str]:
    """Return markets for cross-validation: all high-volume + random sample of the rest."""
    mp = storage._markets_path()
    if not mp.exists():
        return []

    tbl = pq.read_table(mp, columns=["condition_id", "total_volume_usd"])
    high_value = set(
        row["condition_id"]
        for row in tbl.to_pylist()
        if (row["total_volume_usd"] or 0) >= min_volume
    )
    low_value = [m for m in market_ids if m not in high_value]
    sampled_low = random.sample(low_value, k=max(0, int(len(low_value) * sample_rate)))
    return list(high_value) + sampled_low


def _reconcile(
    condition_id: str,
    onchain_trades: List[Trade],
    storage: Storage,
) -> List[Dict[str, Any]]:
    """Compare on-chain trades against Data API trades for the same market."""
    discrepancies = []
    mp = storage._markets_path()
    if not mp.exists():
        return discrepancies

    # Load existing trade_ids for this market from Parquet
    trades_root = storage.root / "trades"
    if not trades_root.exists():
        return discrepancies

    import duckdb
    con = duckdb.connect()
    try:
        existing_ids = set(
            row[0]
            for row in con.execute(
                f"SELECT trade_id FROM read_parquet('{trades_root}/**/*.parquet') WHERE condition_id = ?",
                [condition_id],
            ).fetchall()
        )
    except Exception:
        existing_ids = set()

    for t in onchain_trades:
        if t.trade_id not in existing_ids:
            discrepancies.append(
                {
                    "type": "missing_in_data_api",
                    "condition_id": condition_id,
                    "trade_id": t.trade_id,
                }
            )

    return discrepancies


def run(cfg: Config, storage: Storage) -> List[Dict[str, Any]]:
    """Run Stage C. Returns list of discrepancy records."""
    market_ids = storage.list_market_ids()
    sampled = _sample_markets(
        market_ids,
        cfg.ingestion.cross_validation_sample_rate,
        cfg.ingestion.cross_validation_min_volume_usd,
        storage,
    )

    if not sampled:
        log.info("Stage C: no markets to cross-validate")
        return []

    if not cfg.ingestion.polygon_rpc:
        log.warning("Stage C: POLYGON_RPC_URL not set, skipping on-chain validation")
        return []

    rpc = PolygonRPCClient(cfg.ingestion, cfg.migration)
    all_discrepancies: List[Dict[str, Any]] = []

    # Load market metadata to determine block ranges
    mp = storage._markets_path()
    meta = {}
    if mp.exists():
        tbl = pq.read_table(mp, columns=["condition_id", "created_ts", "resolution_ts", "contract_version"])
        for row in tbl.to_pylist():
            meta[row["condition_id"]] = row

    for condition_id in sampled:
        m = meta.get(condition_id)
        if not m:
            continue

        try:
            from_block = rpc.ts_to_block(m["created_ts"])
            to_block = rpc.ts_to_block(m["resolution_ts"])
        except Exception as exc:
            log.error("Block lookup failed for %s: %s", condition_id, exc)
            continue

        decoder = v1_decoder if m["contract_version"] == 1 else v2_decoder
        onchain_trades: List[Trade] = []

        try:
            for raw in rpc.iter_order_filled_events(from_block, to_block):
                try:
                    onchain_trades.append(decoder.decode(raw, condition_id))
                except Exception as exc:
                    log.error("Decode error for %s event: %s", condition_id, exc)
        except Exception as exc:
            log.error("RPC error for %s: %s", condition_id, exc)
            continue

        discrepancies = _reconcile(condition_id, onchain_trades, storage)
        if discrepancies:
            log.warning("%s: %d discrepancies found", condition_id, len(discrepancies))
        all_discrepancies.extend(discrepancies)

    rate = len(all_discrepancies) / max(1, len(sampled))
    log.info("Stage C complete: %.2f%% discrepancy rate across %d markets", rate * 100, len(sampled))
    return all_discrepancies
