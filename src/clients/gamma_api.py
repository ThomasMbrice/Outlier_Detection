"""Client for Gamma API — market metadata and resolution outcomes."""

from __future__ import annotations

from typing import Any, Dict, Generator, List

import httpx
from tenacity import retry, stop_after_attempt, wait_exponential

from ..config import IngestionConfig


class GammaClient:
    def __init__(self, cfg: IngestionConfig) -> None:
        self._base = cfg.gamma_api_base.rstrip("/")
        self._client = httpx.Client(timeout=30)

    # ------------------------------------------------------------------
    # Public interface
    # ------------------------------------------------------------------

    def iter_closed_markets(self) -> Generator[Dict[str, Any], None, None]:
        """Page through all closed markets, yielding raw API dicts."""
        offset = 0
        limit = 100
        while True:
            page = self._get_markets(closed=True, offset=offset, limit=limit)
            if page is None or not page:
                break
            yield from page
            if len(page) < limit:
                break
            offset += limit

    def get_market(self, condition_id: str) -> Dict[str, Any]:
        return self._get(f"/markets/{condition_id}")

    # ------------------------------------------------------------------
    # HTTP helpers
    # ------------------------------------------------------------------

    @retry(stop=stop_after_attempt(5), wait=wait_exponential(multiplier=2, min=1, max=30))
    def _get_markets(self, closed: bool, offset: int, limit: int) -> List[Dict[str, Any]]:
        params: Dict[str, Any] = {"closed": str(closed).lower(), "offset": offset, "limit": limit}
        resp = self._client.get(f"{self._base}/markets", params=params)
        if resp.status_code == 422:
            # Gamma API hard-caps pagination around offset 10000; treat as end of results
            return []
        resp.raise_for_status()
        return resp.json()

    @retry(stop=stop_after_attempt(5), wait=wait_exponential(multiplier=2, min=1, max=30))
    def _get(self, path: str) -> Dict[str, Any]:
        resp = self._client.get(f"{self._base}{path}")
        resp.raise_for_status()
        return resp.json()

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> "GammaClient":
        return self

    def __exit__(self, *_: Any) -> None:
        self.close()
