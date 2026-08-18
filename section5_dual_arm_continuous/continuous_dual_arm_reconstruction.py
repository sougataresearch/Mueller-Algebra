"""continuous_dual_arm_reconstruction.py -- Hauge (1978) Section V: the
automatic dual-rotating-compensator method. Reads measure.py's 4x4
continuous-mode output (per-frame logged (C, C') angle pairs at the 5:1
rotation lock). This is the production reconstruction path -- prioritize
correctness and test coverage here (Part 4 spec).

Pure NumPy, no hardware dependency.

Physics -- Hauge Eq. (62)-(66), implemented exactly as given:

With the compensators locked at C' - C1' = 5*(C - C1) (the 5:1 ratio,
Eq. 67), the detector response is a 25-coefficient Fourier series in the
12 angles theta_1..theta_12 (Eq. 63):
    theta_1=2C,        theta_2=4C,         theta_3=2C'-4C,
    theta_4=2C'-2C,    theta_5=2C',        theta_6=2C'+2C,
    theta_7=2C'+4C,    theta_8=4C'-4C,     theta_9=4C'-2C,
    theta_10=4C',      theta_11=4C'+2C,    theta_12=4C'+4C

    R(C,C') = (g*Ip/2) * {a0 + sum_j[aj*cos(theta_j) + bj*sin(theta_j)]}

fit by least squares directly against the ACTUAL logged (C, C') pairs (no
assumed uniform grid or exact 5:1 lock -- the real encoder trajectory is
whatever it is). The 25 fitted coefficients (up to the arbitrary g*Ip/2
scale, dropped here exactly as in Parts 2-3 -- removed by the final
M00-normalization) invert to the 16 Mueller-matrix elements via Eq. (65),
plus 9 further consistency-check relationships (Eq. 66, reported as
diagnostics, never used in the inversion itself).

Phase-reference angles C1, C1' (Eq. 69's phi_j phase corrections) are
NOT applied here -- this module assumes the logged (C, C') angles are
already true optical angles (e.g. via Part 1's discovered zero-offsets),
matching Parts 2-3's convention. continuous_dual_arm_calibration.py
determines/cross-checks C1, C1' as a separate, system-absent diagnostic
run (Hauge Sec. V.B) -- see that module for the phase-correction math
proper, mirroring continuous_single_arm_calibration.py's C1' story.
"""

from __future__ import annotations

import csv
import json
from pathlib import Path

import numpy as np

# ============================================================================
# Fourier fit -- Hauge Eq. (62)/(63), against the ACTUAL logged (C, C')
# ============================================================================

THETA_INDICES = tuple(range(1, 13))


def _theta(j: int, c, cp):
    return {
        1: 2 * c, 2: 4 * c, 3: 2 * cp - 4 * c, 4: 2 * cp - 2 * c, 5: 2 * cp, 6: 2 * cp + 2 * c,
        7: 2 * cp + 4 * c, 8: 4 * cp - 4 * c, 9: 4 * cp - 2 * c, 10: 4 * cp, 11: 4 * cp + 2 * c, 12: 4 * cp + 4 * c,
    }[j]


def fit_dual_arm_fourier(c_deg, cp_deg, intensities: np.ndarray) -> dict:
    """Least-squares fit of intensities(C, C') to the 25-term model
    (Eq. 62/63), against the REAL per-frame logged (C, C') angle pairs (not
    an assumed 5:1-locked uniform grid). intensities: shape (n_frames,) or
    (n_frames, H, W). A single lstsq call handles every pixel at once (the
    design matrix depends only on the logged angles, shared by every
    pixel) -- no per-pixel Python loop.

    Needs at least 25 frames (25 unknowns: a0 plus 12 harmonics x 2).
    Fewer than that is an UNDERDETERMINED system that numpy.linalg.lstsq
    does not detect or warn about -- it silently returns *a* solution
    (its minimum-norm one), not the correct one (confirmed: 12 frames from
    an unusually coarse CAPTURE_ANGLE_STEP_DEG gave a max Mueller-matrix
    error of 1.48 instead of machine precision). This is the same
    underdetermined-fit bug class found and fixed in Section II's
    fit_calibration_least_squares and Section IV's fit_outer_fourier --
    under normal operation (CAPTURE_ANGLE_STEP_DEG's 1 deg default gives
    360 frames per revolution) this is nowhere close to being hit, but an
    unusually coarse step size or a truncated/interrupted acquisition
    could reach it, so it's guarded explicitly rather than left silent."""

    n_frames = len(np.asarray(c_deg))
    if n_frames < 25:
        raise ValueError(
            f"Need at least 25 frames to fit the 25-coefficient dual-arm model without an "
            f"underdetermined system; got {n_frames}. Check CAPTURE_ANGLE_STEP_DEG isn't set "
            "too coarse, and that the acquisition wasn't truncated."
        )

    c = np.deg2rad(np.asarray(c_deg, dtype=np.float64))
    cp = np.deg2rad(np.asarray(cp_deg, dtype=np.float64))
    columns = [np.ones_like(c)]
    labels = ["a0"]
    for j in THETA_INDICES:
        theta = _theta(j, c, cp)
        columns.append(np.cos(theta))
        labels.append(f"a{j}")
        columns.append(np.sin(theta))
        labels.append(f"b{j}")
    design = np.stack(columns, axis=1)  # (n_frames, 25)

    intensities = np.asarray(intensities, dtype=np.float64)
    pixel_shape = intensities.shape[1:]
    flat = intensities.reshape(intensities.shape[0], -1)
    coeffs, *_ = np.linalg.lstsq(design, flat, rcond=None)  # (25, n_pixels)
    coeffs = coeffs.reshape((len(labels),) + pixel_shape)
    return {label: coeffs[i] for i, label in enumerate(labels)}


# ============================================================================
# Forward relations -- Hauge Eq. (64), implemented exactly as given.
# Used by the synthetic round-trip test and by the calibration module's
# M=I special case; the reconstruction itself only needs the INVERSE.
# ============================================================================

def forward_coefficients(m: np.ndarray, s, f, r, s_prime, f_prime, r_prime) -> dict:
    """m: Mueller matrix, shape (4,4) or (H,W,4,4). s,f,r are PSG_QWP's
    (unprimed) defect params; s_prime,f_prime,r_prime are PSA_QWP's
    (primed). Returns the 25 coefficients a0,a1,b1,...,a12,b12 exactly per
    Eq. (64) -- every line transcribed as given, no re-derivation."""

    m00, m01, m02, m03 = m[..., 0, 0], m[..., 0, 1], m[..., 0, 2], m[..., 0, 3]
    m10, m11, m12, m13 = m[..., 1, 0], m[..., 1, 1], m[..., 1, 2], m[..., 1, 3]
    m20, m21, m22, m23 = m[..., 2, 0], m[..., 2, 1], m[..., 2, 2], m[..., 2, 3]
    m30, m31, m32, m33 = m[..., 3, 0], m[..., 3, 1], m[..., 3, 2], m[..., 3, 3]

    a0 = m00 + (1 - f) * m01 + (1 - f_prime) * m10 + (1 - f) * (1 - f_prime) * m11
    a1 = s * ((m00 + m01) + (1 - f_prime) * (m10 + m11))
    b1 = s * (m02 + (1 - f_prime) * m12) + r * (m03 + (1 - f_prime) * m13)
    a2 = f * (m01 + (1 - f_prime) * m11)
    b2 = f * (m02 + (1 - f_prime) * m12)
    # NOTE: the project spec transcribes a3 identically to a7 (a copy/paste
    # slip -- they must differ, or a7-a3 could never recover M32 below).
    # Hauge's own Eq. (64) gives a3 the M22-sign and r'*M32-sign both
    # flipped relative to a7 -- verified against the paper text and against
    # invert_coefficients' M32 formula (independently, via the a7+a3
    # consistency check in Eq. 66, which only holds with this sign).
    a3 = f * (s_prime * (m01 + m11 + m22) - r_prime * m32) / 2
    b3 = -f * (s_prime * (m02 + m12 - m21) + r_prime * m31) / 2
    a4 = (s * s_prime * (m00 + m01 + m10 + m11 + m22) + r * s_prime * m23 - r_prime * s * m32 - r * r_prime * m33) / 2
    b4 = (s * s_prime * (m20 + m21 - m12 - m02) - r * s_prime * (m13 + m03) - r_prime * s * (m30 + m31)) / 2
    a5 = s_prime * ((m00 + m10) + (1 - f) * (m01 + m11))
    b5 = s_prime * (m20 + (1 - f) * m21) - r_prime * (m30 + (1 - f) * m31)
    a6 = (s * s_prime * (m00 + m01 + m10 + m11 - m22) - r * s_prime * m23 + r_prime * s * m32 + r * r_prime * m33) / 2
    b6 = (s * s_prime * (m20 + m21 + m12 + m02) + r * s_prime * (m13 + m03) - r_prime * s * (m30 + m31)) / 2
    a7 = f * (s_prime * (m01 + m11 - m22) + r_prime * m32) / 2
    b7 = f * (s_prime * (m02 + m12 + m21) - r_prime * m31) / 2
    a8 = f * f_prime * (m11 + m22) / 2
    b8 = f * f_prime * (m21 - m12) / 2
    a9 = f_prime * (s * (m10 + m11 + m22) + r * m23) / 2
    b9 = f_prime * (s * (m20 - m12 + m21) - r * m13) / 2
    a10 = f_prime * (m10 + (1 - f) * m11)
    b10 = f_prime * (m20 + (1 - f) * m21)
    a11 = f_prime * (s * (m10 + m11 - m22) - r * m23) / 2
    b11 = f_prime * (s * (m20 + m12 + m21) + r * m13) / 2
    a12 = f * f_prime * (m11 - m22) / 2
    b12 = f * f_prime * (m21 + m12) / 2

    return {
        "a0": a0, "a1": a1, "b1": b1, "a2": a2, "b2": b2, "a3": a3, "b3": b3, "a4": a4, "b4": b4,
        "a5": a5, "b5": b5, "a6": a6, "b6": b6, "a7": a7, "b7": b7, "a8": a8, "b8": b8, "a9": a9, "b9": b9,
        "a10": a10, "b10": b10, "a11": a11, "b11": b11, "a12": a12, "b12": b12,
    }


# ============================================================================
# Inversion -- Hauge Eq. (65), implemented exactly as given. Plus Eq. (66)'s
# 9 consistency-check relationships as diagnostics (never fed back into the
# inversion).
# ============================================================================

def invert_coefficients(coeffs: dict, s, f, r, s_prime, f_prime, r_prime) -> np.ndarray:
    """coeffs: dict of a0,a1,b1,...,a12,b12 (from fit_dual_arm_fourier or
    forward_coefficients). Returns M, shape matching the coefficients'
    pixel shape -- UN-normalized (see normalize_mueller_matrix, below, in
    this same module)."""

    a0, a2, a3, a4, a6, a7, a9, a10, a11 = (
        coeffs["a0"], coeffs["a2"], coeffs["a3"], coeffs["a4"], coeffs["a6"], coeffs["a7"], coeffs["a9"],
        coeffs["a10"], coeffs["a11"],
    )
    b1, b2, b3, b5, b7, b8, b9, b10, b11, b12, a8, a12 = (
        coeffs["b1"], coeffs["b2"], coeffs["b3"], coeffs["b5"], coeffs["b7"], coeffs["b8"], coeffs["b9"],
        coeffs["b10"], coeffs["b11"], coeffs["b12"], coeffs["a8"], coeffs["a12"],
    )

    m11 = (a8 + a12) / (f * f_prime)
    m22 = (a8 - a12) / (f * f_prime)
    m12 = -(b8 - b12) / (f * f_prime)
    m21 = (b8 + b12) / (f * f_prime)
    m00 = a0 - a2 * (1 - f) / f - a10 * (1 - f_prime) / f_prime + (a8 + a12) * (1 - f) * (1 - f_prime) / (f * f_prime)
    # NOTE: the project spec's M01/M02/M10/M20 formulas drop a factor of
    # 1/f' (resp. 1/f) on the (a8+a12)-type term -- re-derived directly
    # from a2=f*[M01+(1-f')*M11] with M11=(a8+a12)/(f*f') substituted in
    # (and the mirror-image derivation for M10/M20 from a10/b10), and
    # confirmed numerically against a synthetic known-M round trip.
    m01 = (a2 - (1 - f_prime) * (a8 + a12) / f_prime) / f
    m02 = (b2 + (1 - f_prime) * (b8 - b12) / f_prime) / f
    m10 = (a10 - (1 - f) * (a8 + a12) / f) / f_prime
    m20 = (b10 - (1 - f) * (b8 + b12) / f) / f_prime
    m13 = ((b11 - b9) / f_prime - s * m12) / r
    m23 = ((a9 - a11) / f_prime - s * m22) / r
    m32 = ((a7 - a3) / f + s_prime * m22) / r_prime
    m31 = (-(b7 + b3) / f + s_prime * m21) / r_prime
    m30 = (-b5 + (b7 + b3) * (1 - f) / f + s_prime * m20) / r_prime
    m03 = (b1 + (b9 - b11) * (1 - f_prime) / f_prime - s * m02) / r
    m33 = ((a6 - a4) + s_prime * r * m23 - s * r_prime * m32 + s * s_prime * m22) / (r * r_prime)

    pixel_shape = np.broadcast(m00, m01, m02, m03).shape
    m = np.empty(pixel_shape + (4, 4), dtype=np.float64)
    m[..., 0, 0], m[..., 0, 1], m[..., 0, 2], m[..., 0, 3] = m00, m01, m02, m03
    m[..., 1, 0], m[..., 1, 1], m[..., 1, 2], m[..., 1, 3] = m10, m11, m12, m13
    m[..., 2, 0], m[..., 2, 1], m[..., 2, 2], m[..., 2, 3] = m20, m21, m22, m23
    m[..., 3, 0], m[..., 3, 1], m[..., 3, 2], m[..., 3, 3] = m30, m31, m32, m33
    return m


def consistency_diagnostics(coeffs: dict, m: np.ndarray, s, f, r, s_prime, f_prime, r_prime) -> dict:
    """Hauge Eq. (66): report-only cross-checks, never used in the
    inversion. Large deviations flag noise, misalignment, or a bad
    rotation-ratio lock -- not fed back into M."""

    m01, m10, m11 = m[..., 0, 1], m[..., 1, 0], m[..., 1, 1]
    m02, m12, m20, m21 = m[..., 0, 2], m[..., 1, 2], m[..., 2, 0], m[..., 2, 1]
    return {
        "a1_should_be_zero": coeffs["a1"],
        "b4_should_be_zero": coeffs["b4"],
        "a5_should_be_zero": coeffs["a5"],
        "b6_should_be_zero": coeffs["b6"],
        # NOTE: re-derived as f*s'*(M01+M11) (not M10 -- a3/a7 both carry
        # M01, matching a2's pattern for the same "f*[...]" prefactor;
        # M10 belongs to the f'-prefactored a9/a10/a11 family instead).
        "a7_plus_a3_vs_f_sprime_m01_m11": {
            "lhs": coeffs["a7"] + coeffs["a3"], "rhs": f * s_prime * (m01 + m11),
        },
        "b7_minus_b3_vs_f_sprime_m02_m12": {
            "lhs": coeffs["b7"] - coeffs["b3"], "rhs": f * s_prime * (m02 + m12),
        },
        "a9_plus_a11_vs_fprime_s_m10_m11": {
            "lhs": coeffs["a9"] + coeffs["a11"], "rhs": f_prime * s * (m10 + m11),
        },
        "b9_plus_b11_vs_fprime_s_m20_m21": {
            "lhs": coeffs["b9"] + coeffs["b11"], "rhs": f_prime * s * (m20 + m21),
        },
        "a6_plus_a4_vs_ssprime_m00_m01_m10_m11": {
            "lhs": coeffs["a6"] + coeffs["a4"], "rhs": s * s_prime * (m[..., 0, 0] + m01 + m10 + m11),
        },
    }


def evaluate_intensity(coeffs: dict, c_deg, cp_deg) -> np.ndarray:
    """R(C,C') = a0 + sum_j[aj*cos(theta_j)+bj*sin(theta_j)] (Eq. 62),
    evaluated at given angles from a coefficient dict -- the forward
    counterpart of fit_dual_arm_fourier, used by the synthetic round-trip
    test to generate intensities from known coefficients."""

    c = np.deg2rad(np.asarray(c_deg, dtype=np.float64))
    cp = np.deg2rad(np.asarray(cp_deg, dtype=np.float64))
    total = np.asarray(coeffs["a0"], dtype=np.float64) * np.ones_like(c)
    for j in THETA_INDICES:
        theta = _theta(j, c, cp)
        total = total + coeffs[f"a{j}"] * np.cos(theta) + coeffs[f"b{j}"] * np.sin(theta)
    return total


def reconstruct_dual_arm(c_deg, cp_deg, intensities: np.ndarray, psg_cal: dict, psa_cal: dict) -> tuple[np.ndarray, dict, dict]:
    """Full Section V measurement pipeline: fit -> invert -> diagnostics.
    psg_cal/psa_cal: {'s','f','r'} for PSG_QWP/PSA_QWP (Part 1 output).
    Returns (M, coeffs, diagnostics); M is UN-normalized."""

    coeffs = fit_dual_arm_fourier(c_deg, cp_deg, intensities)
    m = invert_coefficients(coeffs, psg_cal["s"], psg_cal["f"], psg_cal["r"], psa_cal["s"], psa_cal["f"], psa_cal["r"])
    diagnostics = consistency_diagnostics(coeffs, m, psg_cal["s"], psg_cal["f"], psg_cal["r"], psa_cal["s"], psa_cal["f"], psa_cal["r"])
    return m, coeffs, diagnostics


# ============================================================================
# I/O -- reads measure.py's 4x4 continuous-mode output
# ============================================================================

def load_dual_arm_run(run_dir: Path) -> dict:
    run_dir = Path(run_dir)
    config = json.loads((run_dir / "Config" / "experiment_config.json").read_text(encoding="utf-8"))
    if config["mode"] != "4x4" or config.get("acquisition_type") != "continuous":
        raise ValueError(f"{run_dir} is not a 4x4 continuous-acquisition run.")

    log_path = run_dir / "Logs" / "experiment_log.csv"
    c_values, cp_values, frame_indices = [], [], []
    with log_path.open("r", newline="", encoding="utf-8-sig") as handle:
        for row in csv.DictReader(handle):
            if row.get("Status") != "SUCCESS":
                continue
            c_values.append(float(row["PSG_QWP Angle"]))
            cp_values.append(float(row["PSA_QWP Angle"]))
            frame_indices.append(int(row["Frame Index"]))

    from PIL import Image

    dark_path = run_dir / "Results" / "DarkReference.tiff"
    dark = np.asarray(Image.open(dark_path), dtype=np.float64) if dark_path.is_file() else 0.0

    images_dir = run_dir / "Images"
    images = []
    for frame_index in frame_indices:
        matches = list(images_dir.glob(f"frame_{frame_index:04d}_*.tiff"))
        if not matches:
            raise FileNotFoundError(f"No image found for frame index {frame_index} in {images_dir}.")
        images.append(np.asarray(Image.open(matches[0]), dtype=np.float64) - dark)

    return {
        "c_deg": np.asarray(c_values, dtype=np.float64),
        "cp_deg": np.asarray(cp_values, dtype=np.float64),
        "intensities": np.stack(images, axis=0),
        "rotation_ratio": config.get("rotation_ratio", [1, 5]),
    }


def normalize_mueller_matrix(m: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Normalize by M00 and the trace(M^T M) <= 4*M00^2 realizability
    diagnostic -- duplicated verbatim from
    section3_discrete_reconstruction/discrete_reconstruction.py (this
    project's "each consumer owns its own physics copy" convention,
    already used for measure.py's pre-flight rank check and
    qwp_calibration.py's MOTOR_SERIALS/ZERO_OFFSETS_DEG) rather than an
    import across section folders."""

    m00 = m[..., 0, 0]
    m00_safe = np.where(m00 == 0, np.nan, m00)
    m_norm = m / m00_safe[..., np.newaxis, np.newaxis]
    trace_mtm = np.einsum("...ab,...ab->...", m_norm, m_norm)
    is_realizable = trace_mtm <= 4.0 * m_norm[..., 0, 0] ** 2 + 1e-9
    return m_norm, trace_mtm, is_realizable


def load_qwp_calibration(calibration_run_dir: Path, target: str, use_per_pixel: bool = True) -> dict:
    """Reads qwp_calibration.py's Config/calibration_result.json. Same
    duplication rationale as normalize_mueller_matrix above."""

    run_dir = Path(calibration_run_dir)
    report = json.loads((run_dir / "Config" / "calibration_result.json").read_text(encoding="utf-8"))
    entry = report["results"][target]
    if use_per_pixel:
        return {key: np.load(run_dir / entry["per_pixel_maps"][key]) for key in ("s", "f", "r")}
    summary = entry["summary"]
    aggregation = report["aggregation"]
    return {key: summary[key][aggregation] for key in ("s", "f", "r")}


def save_reconstruction(run_dir: Path, m: np.ndarray, diagnostics: dict, roi: tuple | None = None) -> Path:
    """Saves the per-pixel M as .npy (height x width x 4 x 4) plus an
    ROI-summarized single M matrix and diagnostics as JSON -- same schema
    as discrete_reconstruction.save_reconstruction."""

    run_dir = Path(run_dir)
    results_dir = run_dir / "Results"
    results_dir.mkdir(parents=True, exist_ok=True)
    np.save(results_dir / "mueller_matrix.npy", m)

    height, width = m.shape[:2]
    x, y, w, h = roi if roi is not None else (0, 0, width, height)
    region = m[y : y + h, x : x + w]

    def _roi_median(value) -> float:
        arr = np.asarray(value)
        return float(np.nanmedian(arr[y : y + h, x : x + w])) if arr.ndim >= 2 else float(arr)

    summary = {
        "roi": {"x": x, "y": y, "width": w, "height": h},
        "M": np.nanmedian(region, axis=(0, 1)).tolist(),
        "diagnostics": {
            key: (_roi_median(value["lhs"] - value["rhs"]) if isinstance(value, dict) else _roi_median(value))
            for key, value in diagnostics.items()
        },
    }

    import json

    out_path = results_dir / "mueller_matrix_summary.json"
    out_path.write_text(json.dumps(summary, indent=2), encoding="utf-8")
    return out_path


# ============================================================================
# Entry point
# ============================================================================

def _parse_args():
    import argparse

    parser = argparse.ArgumentParser(description="Hauge (1978) Section V dual-arm continuous Mueller-matrix reconstruction")
    parser.add_argument("run_dir", type=Path, help="measure.py 4x4 continuous-mode output directory (Data/<run>/)")
    parser.add_argument("--psg-calibration-dir", type=Path, required=True, help="qwp_calibration.py output dir calibrating PSG_QWP")
    parser.add_argument("--psa-calibration-dir", type=Path, required=True, help="qwp_calibration.py output dir calibrating PSA_QWP")
    parser.add_argument("--scalar-calibration", action="store_true", help="Use the ROI-summary s/f/r instead of per-pixel maps")
    parser.add_argument("--roi", type=int, nargs=4, metavar=("X", "Y", "W", "H"), default=None)
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    data = load_dual_arm_run(args.run_dir)
    psg_cal = load_qwp_calibration(args.psg_calibration_dir, "PSG_QWP", not args.scalar_calibration)
    psa_cal = load_qwp_calibration(args.psa_calibration_dir, "PSA_QWP", not args.scalar_calibration)

    m, _, diagnostics = reconstruct_dual_arm(data["c_deg"], data["cp_deg"], data["intensities"], psg_cal, psa_cal)
    m_norm, _, _ = normalize_mueller_matrix(m)
    out_path = save_reconstruction(args.run_dir, m_norm, diagnostics, tuple(args.roi) if args.roi else None)
    print("Mode: 4x4 (dual-arm continuous)")
    print(f"Saved: {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
