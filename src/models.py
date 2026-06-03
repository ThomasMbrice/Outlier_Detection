"""Canonical data models for the five tables in the dataset."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any, Dict, List, Optional


class Side(str, Enum):
    YES_BUY = "YES_BUY"
    YES_SELL = "YES_SELL"
    NO_BUY = "NO_BUY"
    NO_SELL = "NO_SELL"


class TradeSource(str, Enum):
    DATA_API = "DATA_API"
    ONCHAIN = "ONCHAIN"


class PriceSource(str, Enum):
    TRADE_INFERRED = "TRADE_INFERRED"
    ORDERBOOK = "ORDERBOOK"


@dataclass
class Market:
    condition_id: str
    question: str
    category: str
    subcategory: Optional[str]
    created_ts: int
    end_ts: int
    resolution_ts: int
    resolution_outcome: int  # 0 or 1
    contract_version: int    # 1 or 2
    migration_split: bool
    unique_traders: int
    total_volume_usd: float


@dataclass
class Trade:
    trade_id: str           # {tx_hash}:{log_index}
    condition_id: str
    block_ts: int
    taker_wallet: str
    maker_wallet: str
    side: Side
    price: float            # [0, 1]
    size_shares: float
    size_usd: float         # price * size_shares
    tx_hash: str
    source: TradeSource


@dataclass
class MarketPrice:
    condition_id: str
    ts: int
    mid_price: float
    bid: float
    ask: float
    source: PriceSource


@dataclass
class IngestionRun:
    run_id: str
    start_ts: int
    end_ts: int
    markets_added: int
    trades_added: int
    wallets_seen: int
    errors: List[Dict[str, Any]]
    git_commit: str


# Parquet schemas (column name → pyarrow type string) used in storage.py
MARKETS_SCHEMA = {
    "condition_id": "string",
    "question": "string",
    "category": "string",
    "subcategory": "string",
    "created_ts": "int64",
    "end_ts": "int64",
    "resolution_ts": "int64",
    "resolution_outcome": "int8",
    "contract_version": "int8",
    "migration_split": "bool_",
    "unique_traders": "int32",
    "total_volume_usd": "float64",
}

TRADES_SCHEMA = {
    "trade_id": "string",
    "condition_id": "string",
    "block_ts": "int64",
    "taker_wallet": "string",
    "maker_wallet": "string",
    "side": "string",
    "price": "float64",
    "size_shares": "float64",
    "size_usd": "float64",
    "tx_hash": "string",
    "source": "string",
}

MARKET_PRICES_SCHEMA = {
    "condition_id": "string",
    "ts": "int64",
    "mid_price": "float64",
    "bid": "float64",
    "ask": "float64",
    "source": "string",
}

INGESTION_LOG_SCHEMA = {
    "run_id": "string",
    "start_ts": "int64",
    "end_ts": "int64",
    "markets_added": "int32",
    "trades_added": "int32",
    "wallets_seen": "int32",
    "errors": "string",   # JSON blob
    "git_commit": "string",
}
