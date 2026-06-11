"""Pipeline orchestrator. `python -m sketchhound.run` executes one full run:

    fetch (eBay) -> normalize -> dedup -> gate -> vision score
        -> persist -> publish page -> push hot alerts

Guardrails enforced here (brief section 6):
- Vision pool = new gate survivors + previously persisted GATE_SURVIVOR rows
  (the backfill drain), capped at BACKFILL_VISION_CAP per run.
- Abort: >ABORT_NEW_SURVIVORS new survivors this run -> skip vision entirely,
  publish the feed with a warning banner.
- Stage failures append to run errors but never abandon the run: the page is
  republished and the run row recorded even on a partial run.
"""

from __future__ import annotations

import argparse
from pathlib import Path

from . import config


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m sketchhound.run")
    parser.add_argument("--watchlist", type=Path, default=config.DEFAULT_WATCHLIST)
    parser.add_argument("--db", type=Path, default=config.DEFAULT_DB)
    parser.add_argument("--site-dir", type=Path, default=config.DEFAULT_SITE_DIR)
    parser.add_argument(
        "--skip-vision",
        action="store_true",
        help="Run fetch/dedup/persist/publish without model calls (step-2 verification mode)",
    )
    args = parser.parse_args(argv)

    # Step 2: secrets + watchlist + DB connect; fetch -> normalize -> dedup -> persist
    # Step 3: gate (negative keywords -> Haiku for artist queries) -> vision (capped)
    # Step 4: select sections -> render -> publish to docs/
    # Step 5: push ntfy alerts for newly-hot listings (alerted_at guard)
    raise NotImplementedError("Pipeline lands in build steps 2-5")


if __name__ == "__main__":
    raise SystemExit(main())
