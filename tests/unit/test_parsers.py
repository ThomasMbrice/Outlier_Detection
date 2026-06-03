"""Unit tests for parsers, normalizers, and price-bucketing functions."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

FIXTURES = Path(__file__).parent.parent / "fixtures"


# ------------------------------------------------------------------
# Decoder cross-version parity
# ------------------------------------------------------------------

def _load_onchain() -> dict:
    return json.loads((FIXTURES / "sample_onchain_log.json").read_text())


def test_v1_decoder_produces_valid_trade():
    from src.decoders import v1_decoder
    from src.models import TradeSource

    raw = _load_onchain()
    trade = v1_decoder.decode(raw, condition_id="0xabc")

    assert trade.trade_id == f"{raw['tx_hash']}:{raw['log_index']}"
    assert 0.0 <= trade.price <= 1.0
    assert trade.size_shares > 0
    assert trade.size_usd > 0
    assert trade.source == TradeSource.ONCHAIN


def test_v2_decoder_produces_valid_trade():
    from src.decoders import v2_decoder
    from src.models import TradeSource

    raw = _load_onchain()
    raw["contract_version"] = 2  # same economic content
    trade = v2_decoder.decode(raw, condition_id="0xabc")

    assert trade.trade_id == f"{raw['tx_hash']}:{raw['log_index']}"
    assert 0.0 <= trade.price <= 1.0
    assert trade.source == TradeSource.ONCHAIN


def test_v1_v2_identical_economic_content_produces_identical_rows():
    """V1 and V2 decoders must produce identical normalized rows for identical economics."""
    from src.decoders import v1_decoder, v2_decoder

    raw = _load_onchain()
    condition_id = "0xabc"
    t1 = v1_decoder.decode(raw, condition_id)
    t2 = v2_decoder.decode(raw, condition_id)

    assert t1.trade_id == t2.trade_id
    assert t1.price == pytest.approx(t2.price, abs=1e-9)
    assert t1.size_shares == pytest.approx(t2.size_shares, abs=1e-9)
    assert t1.size_usd == pytest.approx(t2.size_usd, abs=1e-9)
    assert t1.side == t2.side
    assert t1.taker_wallet == t2.taker_wallet
    assert t1.maker_wallet == t2.maker_wallet


# ------------------------------------------------------------------
# Price series bucketing
# ------------------------------------------------------------------

def test_vwap_basic():
    from src.stages.stage_e_price_series import _vwap

    price = _vwap([0.6, 0.7], [100.0, 200.0])
    expected = (0.6 * 100 + 0.7 * 200) / 300
    assert price == pytest.approx(expected, abs=1e-9)


def test_vwap_zero_size():
    from src.stages.stage_e_price_series import _vwap

    assert _vwap([], []) == 0.5


def test_interpolation_fills_short_gaps():
    from src.stages.stage_e_price_series import _interpolate

    buckets = {
        0: {"condition_id": "0xabc", "vwap": 0.5},
        180: {"condition_id": "0xabc", "vwap": 0.8},
    }
    result = _interpolate(buckets, bucket_seconds=60, max_gap=21600)
    tss = [r.ts for r in result]
    assert 60 in tss
    assert 120 in tss


def test_interpolation_skips_large_gaps():
    from src.stages.stage_e_price_series import _interpolate

    buckets = {
        0: {"condition_id": "0xabc", "vwap": 0.5},
        # 25 hours apart — beyond max_gap
        90000: {"condition_id": "0xabc", "vwap": 0.8},
    }
    result = _interpolate(buckets, bucket_seconds=60, max_gap=21600)
    tss = [r.ts for r in result]
    # No fill rows should exist between 0 and 90000
    fill_tss = [t for t in tss if 0 < t < 90000]
    assert fill_tss == []


# ------------------------------------------------------------------
# Category derivation
# ------------------------------------------------------------------

def test_category_politics():
    from src.stages.stage_a_discovery import _derive_category

    cat, _ = _derive_category("Will Biden win the election?")
    assert cat == "politics"


def test_category_crypto():
    from src.stages.stage_a_discovery import _derive_category

    cat, _ = _derive_category("Will Bitcoin hit 100k?")
    assert cat == "crypto"


def test_category_other():
    from src.stages.stage_a_discovery import _derive_category

    cat, _ = _derive_category("Will pigs fly in 2027?")
    assert cat == "other"
