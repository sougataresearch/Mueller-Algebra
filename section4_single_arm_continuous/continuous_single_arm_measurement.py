"""continuous_single_arm_measurement.py -- Section IV's own capture main.
A thin CLI wrapper around common/measure.py, presetting
--acquisition continuous; --mode restricted to 3x4/4x3 (the two modes
with exactly one QWP in the beam -- continuous_single_arm_reconstruction.MODE_TABLE's
own keys -- see common/README.md's mode table for why 4x4 and 3x3 don't
belong to this section).

Never reimplements acquisition -- calls measure.py's own
run_fresh_session() directly, the same function
`python ../common/measure.py --acquisition continuous --mode {3x4,4x3} ...`
runs. This file exists so section4_single_arm_continuous/ visibly
contains both of its own main files: this one (capture) and
continuous_single_arm_reconstruction.py (reconstruct from a folder path).
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "common"))
import measure  # noqa: E402

#: Matches continuous_single_arm_reconstruction.MODE_TABLE's own keys --
#: hardcoded here (not imported) to avoid coupling this thin wrapper to
#: that module's internals for two literal strings.
MODES = ("3x4", "4x3")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Section IV single-arm continuous-mode acquisition (wraps common/measure.py)"
    )
    parser.add_argument("--mode", choices=MODES, default="3x4")
    parser.add_argument("--run-label", default=measure.RUN_LABEL)
    parser.add_argument("--dry-run", action="store_true", default=measure.DRY_RUN)
    parser.add_argument("--no-prompt", action="store_true", help="Skip confirmation prompts; one sample, then exit.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    return measure.run_fresh_session(args.mode, "continuous", args.run_label, args.dry_run, args.no_prompt)


if __name__ == "__main__":
    raise SystemExit(main())
