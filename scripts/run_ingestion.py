#!/usr/bin/env python3
"""Entry point for running the full ingestion pipeline.

Usage:
    python scripts/run_ingestion.py [--config config.yaml] [--log-level INFO]
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

# Allow running from the repo root without installing the package
sys.path.insert(0, str(Path(__file__).parent.parent))

from src.config import load_config
from src.ingestion import run


def main() -> None:
    parser = argparse.ArgumentParser(description="Run Polymarket ingestion pipeline")
    parser.add_argument("--config", default="config.yaml", help="Path to config YAML")
    parser.add_argument("--log-level", default="INFO", help="Logging level")
    args = parser.parse_args()

    logging.basicConfig(
        level=args.log_level.upper(),
        format="%(asctime)s %(levelname)s %(name)s — %(message)s",
    )

    cfg = load_config(args.config)
    result = run(cfg)

    if result.errors:
        logging.warning("%d errors recorded — check ingestion_log for details", len(result.errors))
        sys.exit(1)


if __name__ == "__main__":
    main()
