"""qwp_calibration_reconstruction.py -- Hauge (1978) Section II,
RECONSTRUCTION ONLY: reads a folder produced by
qwp_calibration_capture.py (or qwp_calibration.py's combined flow) and
runs the closed-form (or N-angle least-squares) retardance solve --
Eq. 19-21/Eq. 4 -- entirely from saved images. No hardware, no motors,
no camera; takes a folder path, same CLI shape as
discrete_reconstruction.py/continuous_single_arm_reconstruction.py/
continuous_dual_arm_reconstruction.py already have for Sections III/IV/V.

This is qwp_calibration.py's run_reconstruction() (see that module for
the full implementation) exposed as its own standalone CLI.
"""

from __future__ import annotations

import argparse
from pathlib import Path

import qwp_calibration as qc


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Hauge (1978) Section II reconstruction only: retardance solve from a saved capture folder"
    )
    parser.add_argument(
        "run_dir", type=Path,
        help="qwp_calibration_capture.py (or qwp_calibration.py) output directory (Data/QWP_Calibration/<run>/)",
    )
    parser.add_argument(
        "--aggregation", choices=("mean", "median"), default=qc.AGGREGATION,
        help="ROI summary statistic for the printed report and calibration_result.json (default matches "
        "qwp_calibration.py's own AGGREGATION setting).",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    return qc.run_reconstruction(args.run_dir, args.aggregation)


if __name__ == "__main__":
    raise SystemExit(main())
