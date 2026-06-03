"""Load and validate the YAML configuration."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import List

import yaml


@dataclass
class IngestionConfig:
    data_api_base: str
    gamma_api_base: str
    polygon_rpc: str
    rate_limit_rps: float = 2.0
    retry_max_attempts: int = 5
    retry_backoff_base_sec: float = 2.0
    cross_validation_sample_rate: float = 0.05
    cross_validation_min_volume_usd: float = 100_000.0


@dataclass
class StorageConfig:
    root_path: Path
    parquet_compression: str = "zstd"
    wallet_partition_bits: int = 8


@dataclass
class PriceSeriesConfig:
    bucket_seconds: int = 60
    max_interpolation_gap_seconds: int = 21_600


@dataclass
class MigrationConfig:
    v1_v2_cutover_ts: int = 1_745_841_600  # 2026-04-28 12:00 UTC
    v1_exchange_addresses: List[str] = field(default_factory=list)
    v2_exchange_addresses: List[str] = field(default_factory=list)


@dataclass
class Config:
    ingestion: IngestionConfig
    storage: StorageConfig
    price_series: PriceSeriesConfig
    migration: MigrationConfig


def load_config(path: str | Path = "config.yaml") -> Config:
    raw = yaml.safe_load(Path(path).read_text())

    # Expand env vars in polygon_rpc
    polygon_rpc = raw["ingestion"]["polygon_rpc"]
    if polygon_rpc.startswith("${") and polygon_rpc.endswith("}"):
        env_key = polygon_rpc[2:-1]
        polygon_rpc = os.environ.get(env_key, "")

    ing = raw["ingestion"]
    stor = raw["storage"]
    ps = raw["price_series"]
    mig = raw["migration"]

    return Config(
        ingestion=IngestionConfig(
            data_api_base=ing["data_api_base"],
            gamma_api_base=ing["gamma_api_base"],
            polygon_rpc=polygon_rpc,
            rate_limit_rps=ing.get("rate_limit_rps", 2.0),
            retry_max_attempts=ing.get("retry_max_attempts", 5),
            retry_backoff_base_sec=ing.get("retry_backoff_base_sec", 2.0),
            cross_validation_sample_rate=ing.get("cross_validation_sample_rate", 0.05),
            cross_validation_min_volume_usd=ing.get("cross_validation_min_volume_usd", 100_000.0),
        ),
        storage=StorageConfig(
            root_path=Path(stor["root_path"]),
            parquet_compression=stor.get("parquet_compression", "zstd"),
            wallet_partition_bits=stor.get("wallet_partition_bits", 8),
        ),
        price_series=PriceSeriesConfig(
            bucket_seconds=ps.get("bucket_seconds", 60),
            max_interpolation_gap_seconds=ps.get("max_interpolation_gap_seconds", 21_600),
        ),
        migration=MigrationConfig(
            v1_v2_cutover_ts=mig.get("v1_v2_cutover_ts", 1_745_841_600),
            v1_exchange_addresses=mig.get("v1_exchange_addresses", []),
            v2_exchange_addresses=mig.get("v2_exchange_addresses", []),
        ),
    )
