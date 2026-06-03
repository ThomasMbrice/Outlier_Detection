"""Main ingestion orchestrator.

Runs Stages A–E in order, records the run in ingestion_log, and exits
non-zero if validation thresholds are breached.
"""

from __future__ import annotations

import logging
import subprocess
import time
import uuid
from pathlib import Path
from typing import List

from .config import Config, load_config
from .models import IngestionRun
from .stages import stage_a_discovery, stage_b_trades, stage_c_onchain, stage_d_wallet_index, stage_e_price_series
from .storage import Storage

log = logging.getLogger(__name__)


def _git_commit() -> str:
    try:
        return subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip()
    except Exception:
        return "unknown"


def run(cfg: Config) -> IngestionRun:
    storage = Storage(cfg.storage)
    run_id = str(uuid.uuid4())
    start_ts = int(time.time())
    errors: List[dict] = []

    log.info("=== Stage A: Market Discovery ===")
    try:
        markets_added = stage_a_discovery.run(cfg, storage)
        log.info("Stage A complete: %d markets upserted", markets_added)
    except Exception as exc:
        log.error("Stage A failed: %s", exc)
        markets_added = 0
        errors.append({"stage": "A", "error": str(exc)})

    log.info("=== Stage B: Trade Ingestion ===")
    try:
        trades_added = stage_b_trades.run(cfg, storage)
        log.info("Stage B complete: %d trades upserted", trades_added)
    except Exception as exc:
        log.error("Stage B failed: %s", exc)
        trades_added = 0
        errors.append({"stage": "B", "error": str(exc)})

    log.info("=== Stage C: On-Chain Cross-Validation ===")
    try:
        discrepancies = stage_c_onchain.run(cfg, storage)
        if discrepancies:
            errors.extend(discrepancies)
    except Exception as exc:
        log.error("Stage C failed: %s", exc)
        errors.append({"stage": "C", "error": str(exc)})

    log.info("=== Stage D: Wallet Index Build ===")
    try:
        wallets_seen = stage_d_wallet_index.run(cfg, storage)
        log.info("Stage D complete: %d wallets indexed", wallets_seen)
    except Exception as exc:
        log.error("Stage D failed: %s", exc)
        wallets_seen = 0
        errors.append({"stage": "D", "error": str(exc)})

    log.info("=== Stage E: Price Series Generation ===")
    try:
        price_rows = stage_e_price_series.run(cfg, storage)
        log.info("Stage E complete: %d price rows written", price_rows)
    except Exception as exc:
        log.error("Stage E failed: %s", exc)
        errors.append({"stage": "E", "error": str(exc)})

    end_ts = int(time.time())
    run = IngestionRun(
        run_id=run_id,
        start_ts=start_ts,
        end_ts=end_ts,
        markets_added=markets_added,
        trades_added=trades_added,
        wallets_seen=wallets_seen,
        errors=errors,
        git_commit=_git_commit(),
    )
    storage.append_run(run)

    log.info(
        "Run %s complete in %ds — %d markets, %d trades, %d wallets, %d errors",
        run_id, end_ts - start_ts, markets_added, trades_added, wallets_seen, len(errors),
    )
    return run
