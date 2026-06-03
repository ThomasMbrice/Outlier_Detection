"""Decode V1 CTF Exchange on-chain logs into canonical Trade rows."""

from __future__ import annotations

from typing import Any, Dict

from ..models import Side, Trade, TradeSource

# V1: makerAssetId == 0 means the maker sold collateral (bought outcome tokens).
# The CTF share denomination is 1e6 (USDC has 6 decimals on Polygon).
_SHARE_DECIMALS = 1_000_000
_COLLATERAL_DECIMALS = 1_000_000


def decode(raw: Dict[str, Any], condition_id: str) -> Trade:
    """Convert a raw V1 on-chain OrderFilled event dict into a Trade."""
    tx_hash = raw["tx_hash"]
    log_index = raw["log_index"]
    trade_id = f"{tx_hash}:{log_index}"

    maker_amount = raw["maker_amount"] / _COLLATERAL_DECIMALS
    taker_amount = raw["taker_amount"] / _SHARE_DECIMALS

    # If maker_asset_id == 0 the maker is selling USDC to buy outcome shares.
    # Taker is selling shares, i.e. taker is the YES_SELL / NO_SELL side.
    maker_asset_id = raw["maker_asset_id"]

    if maker_asset_id == 0:
        # Maker buys shares; taker sells shares.
        price = maker_amount / taker_amount if taker_amount else 0.0
        side = Side.YES_SELL  # taker side; YES vs NO requires token ID resolution
        size_shares = taker_amount
        size_usd = maker_amount
    else:
        # Maker sells shares; taker buys shares.
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
