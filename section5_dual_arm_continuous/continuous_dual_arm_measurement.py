"""continuous_dual_arm_measurement.py -- Section V's own capture main. A
thin CLI wrapper around common/measure.py, presetting
--mode 4x4 --acquisition continuous fully (Section V is specifically the
4x4 dual-rotating-compensator case -- no mode choice needed at all).

Never reimplements acquisition -- calls measure.py's own
run_fresh_session() directly, the same function
`python ../common/measure.py --mode 4x4 --acquisition continuous ...`
runs. This file exists so section5_dual_arm_continuous/ visibly contains
both of its own main files: this one (capture) and
continuous_dual_arm_reconstruction.py (reconstruct from a folder path).
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "common"))
import measure  # noqa: E402


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Section V dual-arm continuous-mode (4x4) acquisition (wraps common/measure.py)"
    )
    parser.add_argument("--run-label", default=measure.RUN_LABEL)
    parser.add_argument("--dry-run", action="store_true", default=measure.DRY_RUN)
    parser.add_argument("--no-prompt", action="store_true", help="Skip confirmation prompts; one sample, then exit.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    return measure.run_fresh_session("4x4", "continuous", args.run_label, args.dry_run, args.no_prompt)


if __name__ == "__main__":
    raise SystemExit(main())
