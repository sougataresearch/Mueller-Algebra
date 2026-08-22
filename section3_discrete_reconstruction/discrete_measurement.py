"""discrete_measurement.py -- Section III's own capture main. A thin CLI
wrapper around common/measure.py, presetting --acquisition discrete (the
mode this section's own discrete_reconstruction.py reads); --mode still
accepts any of 3x3/3x4/4x3/4x4 since discrete acquisition supports all of
them (see common/README.md's mode table).

Never reimplements acquisition -- calls measure.py's own
run_fresh_session()/resume_discrete_session() directly, the same
functions `python ../common/measure.py --acquisition discrete ...` runs.
This file exists so section3_discrete_reconstruction/ visibly contains
both of its own main files: this one (capture) and
discrete_reconstruction.py (reconstruct from a folder path).
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "common"))
import measure  # noqa: E402


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Section III discrete-mode acquisition (wraps common/measure.py)")
    parser.add_argument("--mode", choices=tuple(measure.MODE_DEFINITIONS), default=measure.MODE)
    parser.add_argument("--run-label", default=measure.RUN_LABEL)
    parser.add_argument(
        "--dry-run", action="store_true", default=None,
        help="Simulate every motor/camera call. If neither --dry-run nor --no-dry-run is given, "
        "you'll be prompted for it interactively (unless --no-prompt is also set).",
    )
    parser.add_argument(
        "--no-dry-run", dest="dry_run", action="store_false",
        help="Explicitly run against real hardware, skipping the interactive dry-run prompt.",
    )
    parser.add_argument("--no-prompt", action="store_true", help="Skip confirmation prompts; one sample, then exit.")
    parser.add_argument("--resume", type=Path, default=None, metavar="RUN_DIRECTORY", help="Resume an interrupted discrete run.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if args.resume:
        return measure.resume_discrete_session(args.resume.resolve())
    return measure.run_fresh_session(args.mode, "discrete", args.run_label, args.dry_run, args.no_prompt)


if __name__ == "__main__":
    raise SystemExit(main())
