"""Decode V2 CTF Exchange on-chain logs into canonical Trade rows.

V2 adds a `fee` field to OrderFilled but the core asset/amount layout is
the same as V1. The unit tests in tests/unit/test_parsers.py assert that
identical economic content produces identical normalized rows regardless of
which decoder is used.
"""

from __future__ import annotations

from typing import Any, Dict

from ..models import Side, Trade, TradeSource

_SHARE_DECIMALS = 1_000_000
_COLLATERAL_DECIMALS = 1_000_000


def decode(raw: Dict[str, Any], condition_id: str) -> Trade:
    """Convert a raw V2 on-chain OrderFilled event dict into a Trade."""
    tx_hash = raw["tx_hash"]
    log_index = raw["log_index"]
    trade_id = f"{tx_hash}:{log_index}"

    maker_amount = raw["maker_amount"] / _COLLATERAL_DECIMALS
    taker_amount = raw["taker_amount"] / _SHARE_DECIMALS
    maker_asset_id = raw["maker_asset_id"]

    if maker_asset_id == 0:
        price = maker_amount / taker_amount if taker_amount else 0.0
        side = Side.YES_SELL
        size_shares = taker_amount
        size_usd = maker_amount
    else:
        price = taker_amount / maker_amount if maker_amount else 0.0
        side = Side.YES_BUY
        size_shares = maker_amount
        size_usd = taker_amount

    return Trade(
        trade_id=trade_id,
        condition_id=condition_id,
        block_ts=raw["block_ts"],
        taker_wallet=raw["taker"].lower(),
        maker_wallet=raw["maker"].lower(),
        side=side,
        price=max(0.0, min(1.0, price)),
        size_shares=size_shares,
        size_usd=size_usd,
        tx_hash=tx_hash,
        source=TradeSource.ONCHAIN,
    )
