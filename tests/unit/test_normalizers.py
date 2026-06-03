"""Unit tests for Data API trade normalization."""

from __future__ import annotations

import pytest

from src.models import Side, TradeSource
from src.stages.stage_b_trades import _parse_trade


def _raw(**overrides) -> dict:
    base = {
        "transactionHash": "0xaabbcc",
        "logIndex": 0,
        "market": "0xcond",
        "timestamp": 1740001000,
        "taker": "0x1111",
        "maker": "0x2222",
        "side": "BUY",
        "price": "0.62",
        "size": "1000.0",
    }
    return {**base, **overrides}


def test_parse_trade_basic():
    t = _parse_trade(_raw())
    assert t.trade_id == "0xaabbcc:0"
    assert t.price == pytest.approx(0.62)
    assert t.size_shares == pytest.approx(1000.0)
    assert t.size_usd == pytest.approx(620.0)
    assert t.source == TradeSource.DATA_API
    assert t.side == Side.YES_BUY


def test_parse_trade_sell():
    t = _parse_trade(_raw(side="SELL"))
    assert t.side == Side.YES_SELL


def test_parse_trade_canonical_side_enum():
    t = _parse_trade(_raw(side="NO_BUY"))
    assert t.side == Side.NO_BUY


def test_parse_trade_schema_mismatch_raises():
    bad = _raw(price="not_a_float")
    with pytest.raises(Exception):
        _parse_trade(bad)


def test_parse_trade_wallets_lowercased():
    t = _parse_trade(_raw(taker="0xABCD", maker="0xEFGH"))
    assert t.taker_wallet == "0xabcd"
    assert t.maker_wallet == "0xefgh"
