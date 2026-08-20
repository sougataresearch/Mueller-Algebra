"""qwp_calibration_capture.py -- Hauge (1978) Section II, ACQUISITION ONLY:
null search (Step 0) + per-target compensator null + calibration-angle
image capture (Step 1-4's images). No retardance math here at all -- see
qwp_calibration_reconstruction.py for that half, which reads this
script's own output folder.

This is qwp_calibration.py's run_acquisition() (see that module for the
full implementation and the Hauge-equation citations) exposed as its own
standalone CLI, so "run the whole experiment" and "compute the retardance
from a folder of images" can be two separate steps/processes/sessions --
mirroring the acquisition/reconstruction split every other section
(measure.py + discrete_reconstruction.py, etc.) already has.
qwp_calibration.py's own combined CLI still works unchanged; this script
and qwp_calibration_reconstruction.py are the same two phases, run
separately.
"""

from __future__ import annotations

import argparse
import sys

import qwp_calibration as qc


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Hauge (1978) Section II acquisition only: null search + calibration-angle image capture"
    )
    parser.add_argument(
        "--target", choices=tuple(qc.QWP_ROLES) + ("both",), default="both",
        help="Which QWP(s) to calibrate. 'both' (default) runs PSG_QWP then PSA_QWP back-to-back.",
    )
    parser.add_argument(
        "--null-search-mode", choices=("automated", "interactive"), default=qc.NULL_SEARCH_MODE,
        help="Step-0 null-search strategy. 'automated' (default) is coarse-grid + golden-section; "
        "'interactive' is a manual nudge-and-confirm fallback.",
    )
    parser.add_argument(
        "--calibration-angle-mode", choices=("three_angle", "least_squares"), default=qc.CALIBRATION_ANGLE_MODE,
        help="'three_angle' (default): capture Hauge's minimal 0/45/90 deg set. 'least_squares': capture "
        "--num-calibration-angles evenly-spaced angles instead, for a lower-noise fit downstream.",
    )
    parser.add_argument(
        "--num-calibration-angles", type=int, default=qc.LEAST_SQUARES_NUM_ANGLES,
        help="Number of angles for --calibration-angle-mode least_squares (ignored otherwise). Must be >= 3.",
    )
    parser.add_argument("--dry-run", action="store_true", default=qc.DRY_RUN)
    parser.add_argument("--no-prompt", action="store_true", help="Skip confirmation prompts.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    targets = tuple(qc.QWP_ROLES) if args.target == "both" else (args.target,)
    try:
        run_dir = qc.run_acquisition(
            targets, args.null_search_mode, args.dry_run, args.no_prompt,
            args.calibration_angle_mode, args.num_calibration_angles,
        )
    except (qc.MotorError, qc.CameraError, ValueError, RuntimeError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    if run_dir is None:
        return 1
    print(f"\nRun directory: {run_dir}")
    print(f"Next: python qwp_calibration_reconstruction.py \"{run_dir}\"")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
