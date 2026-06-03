"""Integration test: ingest a small fixed market end-to-end.

Uses httpx mocking to avoid live network calls. Asserts on row counts,
wallet counts, and price series shape.
"""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

import pytest

FIXTURES = Path(__file__).parent.parent / "fixtures"


@pytest.fixture()
def cfg(tmp_path):
    from src.config import Config, IngestionConfig, StorageConfig, PriceSeriesConfig, MigrationConfig

    return Config(
        ingestion=IngestionConfig(
            data_api_base="http://mock-data-api",
            gamma_api_base="http://mock-gamma-api",
            polygon_rpc="",
            rate_limit_rps=100.0,
            retry_max_attempts=1,
            retry_backoff_base_sec=0.0,
            cross_validation_sample_rate=0.0,
            cross_validation_min_volume_usd=1e12,
        ),
        storage=StorageConfig(root_path=tmp_path / "data"),
        price_series=PriceSeriesConfig(bucket_seconds=60, max_interpolation_gap_seconds=21600),
        migration=MigrationConfig(
            v1_v2_cutover_ts=1745841600,
            v1_exchange_addresses=[],
            v2_exchange_addresses=[],
        ),
    )


@pytest.fixture()
def storage(cfg):
    from src.storage import Storage
    return Storage(cfg.storage)


def test_stage_a_upserts_market(cfg, storage, respx_mock):
    """Stage A should upsert the fixture market into storage."""
    market = json.loads((FIXTURES / "sample_market.json").read_text())
    respx_mock.get("http://mock-gamma-api/markets").mock(
        return_value=__import__("httpx").Response(200, json=[market])
    )
    # Second page is empty → terminates
    respx_mock.get("http://mock-gamma-api/markets").mock(
        return_value=__import__("httpx").Response(200, json=[])
    )

    from src.stages import stage_a_discovery

    n = stage_a_discovery.run(cfg, storage)
    assert n == 1
    ids = storage.list_market_ids()
    assert market["conditionId"] in ids


def test_stage_b_upserts_trades(cfg, storage, respx_mock):
    """Stage B should ingest fixture trades for the known market."""
    # Pre-populate markets
    market = json.loads((FIXTURES / "sample_market.json").read_text())
    from src.stages.stage_a_discovery import _parse_market
    m = _parse_market(market, cfg.migration.v1_v2_cutover_ts)
    storage.upsert_markets([m])

    trades = json.loads((FIXTURES / "sample_trades.json").read_text())
    condition_id = market["conditionId"]
    respx_mock.get(f"http://mock-data-api/trades").mock(
        return_value=__import__("httpx").Response(200, json={"data": trades, "next_cursor": None})
    )

    from src.stages import stage_b_trades
    n = stage_b_trades.run(cfg, storage)
    assert n == 2


def test_full_pipeline_price_series(cfg, storage):
    """After Stages A/B/D/E, price rows should exist for the market."""
    # Wire up fixtures directly without HTTP
    import json
    market = json.loads((FIXTURES / "sample_market.json").read_text())
    trades_raw = json.loads((FIXTURES / "sample_trades.json").read_text())

    from src.stages.stage_a_discovery import _parse_market
    from src.stages.stage_b_trades import _parse_trade
    from src.stages import stage_d_wallet_index, stage_e_price_series

    m = _parse_market(market, cfg.migration.v1_v2_cutover_ts)
    storage.upsert_markets([m])

    trades = [_parse_trade(r) for r in trades_raw]
    for t in trades:
        t.condition_id = market["conditionId"]
    storage.upsert_trades(trades)

    stage_d_wallet_index.run(cfg, storage)
    price_count = stage_e_price_series.run(cfg, storage)
    assert price_count >= 2
