"""Stage A — Market Discovery.

Queries Gamma API for all closed markets, derives category + contract_version,
and upserts into the markets table.
"""

from __future__ import annotations

import re
from typing import Any, Dict, Optional

from ..clients.gamma_api import GammaClient
from ..config import Config
from ..models import Market
from ..storage import Storage

# Coarse category taxonomy — keyword / regex matching on question text
_CATEGORY_RULES = [
    ("politics", re.compile(r"\belection|president|congress|senate|trump|biden|vote\b", re.I)),
    ("crypto", re.compile(r"\bbtc|eth|bitcoin|ethereum|crypto|token|defi|nft\b", re.I)),
    ("sports", re.compile(r"\bnfl|nba|mlb|nhl|soccer|football|basketball|tennis|golf\b", re.I)),
    ("macro", re.compile(r"\bfed|interest rate|inflation|gdp|recession|cpi|fomc\b", re.I)),
    ("weather", re.compile(r"\bhurricane|tornado|earthquake|flood|storm|weather\b", re.I)),
]


def _derive_category(question: str) -> tuple[str, Optional[str]]:
    for category, pattern in _CATEGORY_RULES:
        if pattern.search(question):
            return category, None
    return "other", None


def _derive_resolution_outcome(raw: Dict[str, Any]) -> Optional[int]:
    """Infer binary resolution from outcomePrices.

    At resolution, the winning outcome settles to 1.0 and the loser to 0.0.
    outcomePrices is a JSON-encoded list of price strings, e.g. '["1", "0"]'.
    Returns None if the outcome cannot be determined (market not yet resolved).
    """
    import json as _json

    prices_raw = raw.get("outcomePrices")
    if not prices_raw:
        return None
    try:
        prices = _json.loads(prices_raw) if isinstance(prices_raw, str) else prices_raw
        first = float(prices[0])
        if first >= 0.99:
            return 1
        elif first <= 0.01:
            return 0
    except (TypeError, ValueError, IndexError, KeyError):
        pass
    return None


def _parse_ts(val: Any) -> int:
    """Parse a timestamp that may be a unix number or an ISO/non-standard string."""
    from datetime import datetime, timezone

    if val is None:
        return 0
    if isinstance(val, (int, float)):
        return int(val)
    if isinstance(val, str):
        # Normalise: "2020-11-02 16:31:01+00" → "+00:00"; "Z" → "+00:00"
        s = val.strip().replace("Z", "+00:00")
        if s.endswith("+00"):
            s = s[:-3] + "+00:00"
        elif s.endswith("-00"):
            s = s[:-3] + "+00:00"
        try:
            return int(datetime.fromisoformat(s).timestamp())
        except ValueError:
            pass
        try:
            return int(float(val))
        except (TypeError, ValueError):
            pass
    return 0


def _derive_resolution_ts(raw: Dict[str, Any]) -> int:
    """closedTime is the actual resolution timestamp; fall back to endDate."""
    for field in ("closedTime", "resolutionTime", "endDate"):
        ts = _parse_ts(raw.get(field))
        if ts:
            return ts
    return 0


def _derive_contract_version(raw: Dict[str, Any], cutover_ts: int) -> tuple[int, bool]:
    created_ts = _parse_ts(raw.get("createdAt"))
    resolution_ts = _derive_resolution_ts(raw)

    before_cutover = created_ts < cutover_ts
    after_cutover = resolution_ts >= cutover_ts

    migration_split = before_cutover and after_cutover
    contract_version = 1 if created_ts < cutover_ts else 2
    return contract_version, migration_split


def _parse_market(raw: Dict[str, Any], cutover_ts: int) -> Optional[Market]:
    condition_id = raw.get("conditionId") or raw.get("condition_id")
    if not condition_id:
        return None

    resolution_outcome = _derive_resolution_outcome(raw)
    if resolution_outcome is None:
        return None  # skip unresolved / ambiguous

    question = raw.get("question", "")
    category, subcategory = _derive_category(question)
    contract_version, migration_split = _derive_contract_version(raw, cutover_ts)
    resolution_ts = _derive_resolution_ts(raw)

    return Market(
        condition_id=condition_id,
        question=question,
        category=category,
        subcategory=subcategory,
        created_ts=_parse_ts(raw.get("createdAt")),
        end_ts=_parse_ts(raw.get("endDate")),
        resolution_ts=resolution_ts,
        resolution_outcome=resolution_outcome,
        contract_version=contract_version,
        migration_split=migration_split,
        unique_traders=0,  # not provided by Gamma API; computed post-ingestion
        total_volume_usd=float(raw.get("volumeNum") or raw.get("volume") or 0),
    )


def run(cfg: Config, storage: Storage) -> int:
    """Run Stage A. Returns number of markets upserted."""
    cutover_ts = cfg.migration.v1_v2_cutover_ts
    markets = []

    with GammaClient(cfg.ingestion) as client:
        for raw in client.iter_closed_markets():
            market = _parse_market(raw, cutover_ts)
            if market is not None:
                markets.append(market)

    return storage.upsert_markets(markets)
