"""Client for Polymarket Data API — trade history."""

from __future__ import annotations

import time
from typing import Any, Dict, Generator, List, Optional

import httpx
from tenacity import retry, retry_if_exception, stop_after_attempt, wait_exponential

from ..config import IngestionConfig


def _is_retryable(exc: BaseException) -> bool:
    if isinstance(exc, httpx.HTTPStatusError):
        return exc.response.status_code in (429, 500, 502, 503, 504)
    return isinstance(exc, (httpx.TimeoutException, httpx.NetworkError))


class DataAPIClient:
    def __init__(self, cfg: IngestionConfig) -> None:
        self._base = cfg.data_api_base.rstrip("/")
        self._min_interval = 1.0 / cfg.rate_limit_rps
        self._last_call: float = 0.0
        self._client = httpx.Client(timeout=30)

    # ------------------------------------------------------------------
    # Public interface
    # ------------------------------------------------------------------

    def iter_trades(self, condition_id: str) -> Generator[Dict[str, Any], None, None]:
        """Offset-paginate all trades for a market, yielding raw dicts.

        The Data API returns a plain list (no cursor wrapper). We page via
        offset until we get an empty page.
        """
        offset = 0
        limit = 500
        while True:
            page = self._get_trades_page(condition_id, offset, limit)
            if not page:
                break
            yield from page
            if len(page) < limit:
                break
            offset += limit

    def iter_wallet_trades(self, wallet: str) -> Generator[Dict[str, Any], None, None]:
        cursor: Optional[str] = None
        while True:
            page, next_cursor = self._get_activity_page(wallet, cursor)
            if not page:
                break
            yield from page
            if not next_cursor:
                break
            cursor = next_cursor

    # ------------------------------------------------------------------
    # HTTP helpers
    # ------------------------------------------------------------------

    def _throttle(self) -> None:
        elapsed = time.monotonic() - self._last_call
        if elapsed < self._min_interval:
            time.sleep(self._min_interval - elapsed)
        self._last_call = time.monotonic()

    @retry(
        retry=retry_if_exception(_is_retryable),
        stop=stop_after_attempt(5),
        wait=wait_exponential(multiplier=2, min=1, max=60),
    )
    def _get_trades_page(
        self, condition_id: str, offset: int, limit: int
    ) -> List[Dict[str, Any]]:
        self._throttle()
        params: Dict[str, Any] = {"market": condition_id, "limit": limit, "offset": offset}
        resp = self._client.get(f"{self._base}/trades", params=params)
        if resp.status_code == 429:
            retry_after = int(resp.headers.get("Retry-After", "5"))
            time.sleep(retry_after)
            resp.raise_for_status()
        resp.raise_for_status()
        data = resp.json()
        # API returns a plain list; guard against dict-wrapped responses
        if isinstance(data, dict):
            return data.get("data", [])
        return data if isinstance(data, list) else []

    @retry(
        retry=retry_if_exception(_is_retryable),
        stop=stop_after_attempt(5),
        wait=wait_exponential(multiplier=2, min=1, max=60),
    )
    def _get_activity_page(
        self, wallet: str, cursor: Optional[str]
    ) -> tuple[List[Dict[str, Any]], Optional[str]]:
        self._throttle()
        params: Dict[str, Any] = {"user": wallet, "limit": 500}
        if cursor:
            params["cursor"] = cursor
        resp = self._client.get(f"{self._base}/activity", params=params)
        resp.raise_for_status()
        data = resp.json()
        return data.get("data", []), data.get("next_cursor")

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> "DataAPIClient":
        return self

    def __exit__(self, *_: Any) -> None:
        self.close()
