"""Stage B — Trade Ingestion via Data API.

For each market not yet fully ingested, pages through the Data API trade
endpoint and upserts into the trades table.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from ..clients.data_api import DataAPIClient
from ..config import Config
from ..models import Side, Trade, TradeSource
from ..storage import Storage

log = logging.getLogger(__name__)

_SIDE_MAP = {
    "BUY": Side.YES_BUY,
    "SELL": Side.YES_SELL,
    "YES_BUY": Side.YES_BUY,
    "YES_SELL": Side.YES_SELL,
    "NO_BUY": Side.NO_BUY,
    "NO_SELL": Side.NO_SELL,
}


def _parse_trade(raw: Dict[str, Any]) -> Optional[Trade]:
    # Data API response fields (confirmed from live API):
    #   transactionHash, conditionId, timestamp (unix int), proxyWallet,
    #   side ("BUY"/"SELL"), price (float), size (float), outcome, outcomeIndex
    # Note: logIndex, taker, maker are not returned — proxyWallet is the trader.
    tx_hash = raw.get("transactionHash", "")
    # No logIndex — use conditionId+outcomeIndex as tie-breaker to keep trade_id unique
    outcome_index = raw.get("outcomeIndex", 0)
    trade_id = f"{tx_hash}:{outcome_index}"

    try:
        price = float(raw["price"])
        size_shares = float(raw.get("size", 0))
        side_raw = str(raw.get("side", "BUY")).upper()
        # Refine side using outcomeIndex: 0 = YES token, 1 = NO token
        if outcome_index == 1:
            side = Side.NO_BUY if side_raw == "BUY" else Side.NO_SELL
        else:
            side = Side.YES_BUY if side_raw == "BUY" else Side.YES_SELL
        size_usd = price * size_shares
    except (KeyError, TypeError, ValueError) as exc:
        log.error("Schema mismatch parsing trade %s: %s — raw: %s", trade_id, exc, raw)
        raise

    wallet = (raw.get("proxyWallet", "") or "").lower()

    return Trade(
        trade_id=trade_id,
        condition_id=raw.get("conditionId", raw.get("market", "")),
        block_ts=int(raw.get("timestamp", 0)),
        taker_wallet=wallet,
        maker_wallet="",   # Data API does not expose maker wallet
        side=side,
        price=price,
        size_shares=size_shares,
        size_usd=size_usd,
        tx_hash=tx_hash,
        source=TradeSource.DATA_API,
    )


def run(cfg: Config, storage: Storage) -> int:
    """Run Stage B. Returns total trades upserted."""
    market_ids = storage.list_market_ids()
    total = 0

    with DataAPIClient(cfg.ingestion) as client:
        for condition_id in market_ids:
            trades: List[Trade] = []
            try:
                for raw in client.iter_trades(condition_id):
                    t = _parse_trade(raw)
                    if t:
                        trades.append(t)
            except Exception as exc:
                log.error("Failed ingesting trades for %s: %s", condition_id, exc)
                continue

            if trades:
                n = storage.upsert_trades(trades)
                total += n
                log.info("market %s: %d trades upserted", condition_id, n)

    return total
