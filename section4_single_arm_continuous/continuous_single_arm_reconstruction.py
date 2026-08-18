"""continuous_single_arm_reconstruction.py -- Hauge (1978) Section IV,
adapted to this project's 3x4/4x3 continuous-rotation modes (one QWP spins,
the other arm's plain polarizer is stepped between revolutions -- see
README.md's mode table and measure.py's continuous-mode docstring).

Pure NumPy, no hardware dependency. Reads measure.py's continuous-mode
output (Images/frame_NNNN_*.tiff, Logs/experiment_log.csv,
Config/experiment_config.json) via continuous_single_arm_calibration's
sibling loader, or is driven directly with in-memory arrays (used by the
synthetic round-trip test).

Physics
-------
One QWP spins continuously while the other arm is a plain polarizer (no
QWP at all in this mode -- see README's mode table: 3x4 = PSG_QWP rotates /
PSA_Analyzer stepped, PSG_Polarizer fixed; 4x3 = PSA_QWP rotates /
PSG_Polarizer stepped, PSA_Analyzer fixed). This method requires the FIXED
companion linear element (PSG_Polarizer for 3x4, PSA_Analyzer for 4x3) to
sit at its calibrated optical 0 -- exactly Hauge Sec. IV.B's own "the
polarizer and analyzer are fixed at P=A=0" premise; Eq. (42)/(43) below are
only valid at that reference (Hauge never generalizes them to a nonzero
fixed angle, and neither do we).

Per-revolution Fourier fit (least-squares against the ACTUAL logged
angles, never an assumed uniform grid) of the rotating side's intensity:
    R(C') = A0 + A2*cos(2C') + B2*sin(2C') + A4*cos(4C') + B4*sin(4C')

Recover the (relative, up to the arbitrary 2/g*Ip scale that final
M00-normalization removes) output vector at that outer-axis step, using
the ROTATING side's own measured s,f,r (Hauge Eq. 43):
    V0 = A0 - A4*(1-f)/f
    V1 = A4/f
    V2 = B4/f
    V3 = (-B2 + B4*(s/f)) / r

Repeat across all outer-axis steps; second Fourier pass fits each of
V0..V3 against the OUTER angle, same 5-basis-function model, giving the
4x5 matrix E (Hauge Eq. 46/49). The known 4x5 matrix B comes from the
OUTER (non-rotating) side's own Part-2-style generator/analyzer formula,
expanded into the same 5 harmonic coefficients (Hauge Eq. 44 generalizes
this beyond his own P=0 special case to any FIXED companion angle on that
side, though for our two modes the outer side is a plain polarizer with no
companion at all, so B reduces to Eq. (7)'s trivial form -- see
harmonic_matrix_polarizer()). F = B^+ (Moore-Penrose pseudo-inverse, via
SVD -- robust to B's structurally rank-deficient row/column for a
no-QWP side, unlike a literal B^T(BB^T)^-1 which would need to invert a
singular matrix).

Which side of "E = M @ B" (Hauge Eq. 49) M lands on depends on whether the
ROTATING axis plays the generator role (3x4: PSG_QWP) or the analyzer role
(4x3: PSA_QWP, matching Hauge's own convention directly) -- the two modes
are mirror images of each other, so M = E @ F for 4x3, and M = (E @ F)^T
for 3x4 (see reconstruct_single_arm's `mode` dispatch). Implemented as ONE
function parameterized by which side rotates, not duplicated.
"""

from __future__ import annotations

import csv
import json
from pathlib import Path

import numpy as np

MODE_TABLE = {
    "3x4": {"rotating_axis": "PSG_QWP", "outer_axis": "PSA_Analyzer", "fixed_axis": "PSG_Polarizer", "rotating_role": "generator"},
    "4x3": {"rotating_axis": "PSA_QWP", "outer_axis": "PSG_Polarizer", "fixed_axis": "PSA_Analyzer", "rotating_role": "analyzer"},
}

# The outer (non-rotating) side's own arity, matching discrete_reconstruction's
# _vector_arity convention: neither 3x4's PSA_Analyzer nor 4x3's PSG_Polarizer
# carries a QWP, so both only span a 3-D subspace.
_OUTER_HAS_QWP = False


# ============================================================================
# First Fourier pass: per-revolution fit against the ACTUAL logged angles
# ============================================================================

def fit_revolution_fourier(angles_deg, intensities: np.ndarray) -> dict:
    """Least-squares fit of intensities(angles_deg) to the 5-term model
    A0 + A2 cos(2t) + B2 sin(2t) + A4 cos(4t) + B4 sin(4t). angles_deg:
    shape (n_frames,) of the REAL per-frame logged rotating-axis angles
    (not an assumed uniform grid). intensities: shape (n_frames,) or
    (n_frames, H, W). A single lstsq call handles every pixel at once (the
    design matrix depends only on the logged angles, shared by every
    pixel) -- no per-pixel Python loop."""

    theta = np.deg2rad(np.asarray(angles_deg, dtype=np.float64))
    design = np.stack(
        [np.ones_like(theta), np.cos(2 * theta), np.sin(2 * theta), np.cos(4 * theta), np.sin(4 * theta)], axis=1
    )
    intensities = np.asarray(intensities, dtype=np.float64)
    pixel_shape = intensities.shape[1:]
    flat = intensities.reshape(intensities.shape[0], -1)
    coeffs, *_ = np.linalg.lstsq(design, flat, rcond=None)
    coeffs = coeffs.reshape((5,) + pixel_shape)
    return {"A0": coeffs[0], "A2": coeffs[1], "B2": coeffs[2], "A4": coeffs[3], "B4": coeffs[4]}


def recover_rotating_side_vector(fourier: dict, s, f, r, rotating_role: str) -> np.ndarray:
    """Hauge Eq. (43) (the 2/g*Ip scale dropped -- see module docstring;
    final M00-normalization removes it identically to Part 2). s, f, r are
    the ROTATING QWP's own measured defect parameters (Part 1 output).

    Hauge derives Eq. (43) for a rotating compensator playing the ANALYZER
    role (his C', with analyzer_vector's "-r" sign convention -- Part 2's
    Eq. 11 dual). Our 3x4 mode instead has the rotating QWP play the
    GENERATOR role (Part 2's Eq. 8, "+r" convention); re-deriving Eq. 42's
    harmonic decomposition with that sign shows only the V3/S3' term
    flips (the sin(2C) coefficient becomes +r*V3 instead of -r*V3, since
    that's the only row where generator_vector_qwp and analyzer_vector_qwp
    differ in sign) -- V2, V1, V0 are identical either way."""

    a0, a2, b2, a4, b4 = fourier["A0"], fourier["A2"], fourier["B2"], fourier["A4"], fourier["B4"]
    v0 = a0 - a4 * (1.0 - f) / f
    v1 = a4 / f
    v2 = b4 / f
    sign = 1.0 if rotating_role == "analyzer" else -1.0
    v3 = sign * (-b2 + b4 * (s / f)) / r
    return np.stack(np.broadcast_arrays(v0, v1, v2, v3), axis=0)


# ============================================================================
# Second Fourier pass: fit V0..V3 vs the OUTER angle, same 5-basis model
# ============================================================================

def fit_outer_fourier(outer_angles_deg, stacked_v: np.ndarray, num_harmonics: int = 5) -> np.ndarray:
    """stacked_v: shape (n_outer, 4) or (n_outer, 4, H, W) -- one recovered
    4-vector per outer step. Returns E, shape (4, 5) or (4, 5, H, W)
    (always the full 4x5 shape the rest of the pipeline expects -- the
    cos4A/sin4A columns are exact zeros, not fitted, when num_harmonics=3).

    num_harmonics: 5 (default, full {1,cos2A,sin2A,cos4A,sin4A} model)
    needs n_outer >= 5 -- use this only when the OUTER side has a real
    compensator (outer_cal given to reconstruct_single_arm), since only
    then can V(A) genuinely carry cos4A/sin4A dependence.
    num_harmonics=3 (reduced {1,cos2A,sin2A} model, needs n_outer >= 3)
    is analytically exact -- not an approximation -- whenever the outer
    side is a plain polarizer (outer_cal=None, the only case 3x4/4x3
    exercise): harmonic_matrix_polarizer()'s cos4A/sin4A columns are
    already exactly zero, so V(A) cannot carry that dependence either.
    Fitting the full 5-parameter model anyway with n_outer < 5 (e.g.
    measure.py's own OUTER_ANGLES_DEG default of 3 angles) is an
    UNDERDETERMINED system that numpy.linalg.lstsq does not detect or
    warn about -- it silently returns *a* solution (its minimum-norm
    one), not the correct one. This mirrors the exact bug class found
    and fixed in Section II's calibration (fit_calibration_least_squares)."""

    if num_harmonics not in (3, 5):
        raise ValueError(f"num_harmonics must be 3 or 5, got {num_harmonics!r}.")
    n_outer = len(outer_angles_deg)
    if n_outer < num_harmonics:
        raise ValueError(
            f"Need at least {num_harmonics} outer-axis angles to fit a {num_harmonics}-parameter "
            f"model without an underdetermined system; got {n_outer}."
        )

    theta = np.deg2rad(np.asarray(outer_angles_deg, dtype=np.float64))
    if num_harmonics == 5:
        design = np.stack(
            [np.ones_like(theta), np.cos(2 * theta), np.sin(2 * theta), np.cos(4 * theta), np.sin(4 * theta)], axis=1
        )
    else:
        design = np.stack([np.ones_like(theta), np.cos(2 * theta), np.sin(2 * theta)], axis=1)

    pixel_shape = stacked_v.shape[2:]
    flat = stacked_v.reshape(stacked_v.shape[0], -1)  # (n_outer, 4*n_pixels)
    coeffs, *_ = np.linalg.lstsq(design, flat, rcond=None)  # (num_harmonics, 4*n_pixels)
    if num_harmonics == 3:
        zero_pad = np.zeros((2,) + coeffs.shape[1:], dtype=coeffs.dtype)
        coeffs = np.concatenate([coeffs, zero_pad], axis=0)  # (5, 4*n_pixels) -- cos4A/sin4A columns exactly 0
    coeffs = coeffs.reshape((5, 4) + pixel_shape)
    return np.moveaxis(coeffs, 0, 1)  # -> (4, 5) + pixel_shape


# ============================================================================
# Known B matrix -- the OUTER (non-rotating) side's own vector formula
# (Part 2's generator/analyzer formula), expanded in the 5-harmonic basis.
# Always returned as a full 4x5 (padded with a zero row for a no-QWP side,
# per README's mode table -- e.g. no cos4/sin4 dependence, and the S3/d3
# row is identically zero since a plain polarizer never touches circular
# polarization); F = pinv(B) handles that rank deficiency by SVD, not a
# literal (possibly-singular) B^T(BB^T)^-1 inverse.
# ============================================================================

def harmonic_matrix_polarizer() -> np.ndarray:
    """Eq. (7)'s trivial case: the outer side IS the varying polarizer
    itself (no separate fixed companion on that arm at all, unlike
    Hauge's own P-fixed-plus-stepped-C setup) -- {1, cos2X, sin2X, 0}
    expanded directly in the 5-harmonic basis."""

    return np.array(
        [[1.0, 0.0, 0.0, 0.0, 0.0], [0.0, 1.0, 0.0, 0.0, 0.0], [0.0, 0.0, 1.0, 0.0, 0.0], [0.0, 0.0, 0.0, 0.0, 0.0]],
        dtype=np.float64,
    )


def harmonic_matrix_qwp(s, f, r) -> np.ndarray:
    """Hauge Eq. (44): the outer side's own QWP formula (Part 2's
    generator/analyzer-with-QWP form, at ITS fixed companion angle = 0 --
    same premise as the rest of this module), expanded in the 5-harmonic
    basis. Not exercised by 3x4/4x3 (neither has a QWP on the outer side),
    kept for a hypothetical future mode with one."""

    zero = np.zeros_like(np.asarray(s, dtype=np.float64))
    rows = [
        np.stack(np.broadcast_arrays(1.0 + zero, s, zero, zero, zero), axis=0),
        np.stack(np.broadcast_arrays(1.0 - f, s, zero, f, zero), axis=0),
        np.stack(np.broadcast_arrays(zero, zero, s, zero, f), axis=0),
        np.stack(np.broadcast_arrays(zero, zero, r, zero, zero), axis=0),
    ]
    return np.stack(rows, axis=0)


# ============================================================================
# Full reconstruction
# ============================================================================

def reconstruct_single_arm(
    mode: str,
    outer_angles_deg,
    per_outer_rotating_angles_deg,
    per_outer_intensities,
    rotating_cal: dict,
    outer_cal: dict | None = None,
) -> np.ndarray:
    """outer_angles_deg: the n_outer commanded outer-axis angles.
    per_outer_rotating_angles_deg: list of n_outer arrays, the REAL logged
    rotating-axis angle per frame within that revolution.
    per_outer_intensities: list of n_outer arrays, matching shape (each
    (n_frames,) or (n_frames, H, W)).
    rotating_cal: {'s','f','r'} for the ROTATING QWP (Part 1 output).
    outer_cal: {'s','f','r'} for the outer side, only if it has a QWP
    (never true for 3x4/4x3 -- both outer axes are plain polarizers).

    Returns M, shape (4, 4) or (H, W, 4, 4) -- UN-normalized (see
    normalize_mueller_matrix, below, in this same module)."""

    if mode not in MODE_TABLE:
        raise ValueError(f"mode must be one of {tuple(MODE_TABLE)}, got {mode!r}.")
    n_outer = len(outer_angles_deg)
    if len(per_outer_rotating_angles_deg) != n_outer or len(per_outer_intensities) != n_outer:
        raise ValueError("outer_angles_deg, per_outer_rotating_angles_deg, per_outer_intensities must have the same length.")

    rotating_role = MODE_TABLE[mode]["rotating_role"]
    v_stack = []
    for angles, intensities in zip(per_outer_rotating_angles_deg, per_outer_intensities):
        fourier = fit_revolution_fourier(angles, np.asarray(intensities, dtype=np.float64))
        v_stack.append(
            recover_rotating_side_vector(fourier, rotating_cal["s"], rotating_cal["f"], rotating_cal["r"], rotating_role)
        )
    stacked_v = np.stack(v_stack, axis=0)  # (n_outer, 4, ...)

    # Reduced (3-harmonic) fit is analytically exact -- not an
    # approximation -- for a plain-polarizer outer side (outer_cal=None,
    # the only case 3x4/4x3 exercise), and avoids the underdetermined
    # 5-parameter fit that a small n_outer (e.g. measure.py's own
    # OUTER_ANGLES_DEG default of 3) would otherwise silently produce.
    e_mat = fit_outer_fourier(outer_angles_deg, stacked_v, num_harmonics=5 if outer_cal is not None else 3)

    if outer_cal is not None:
        b_mat = harmonic_matrix_qwp(outer_cal["s"], outer_cal["f"], outer_cal["r"])  # (4, 5, ...)
    else:
        b_mat = harmonic_matrix_polarizer()  # (4, 5)
        pixel_shape = e_mat.shape[2:]
        if pixel_shape:
            b_mat = np.broadcast_to(b_mat.reshape(b_mat.shape + (1,) * len(pixel_shape)), b_mat.shape + pixel_shape).copy()

    # Move (4,5) matrix axes to the end so any per-pixel dims broadcast as
    # numpy linalg's leading batch dims (same convention as Part 2).
    e_b = np.moveaxis(e_mat, [0, 1], [-2, -1])
    b_b = np.moveaxis(b_mat, [0, 1], [-2, -1])

    f_b = np.linalg.pinv(b_b)  # (..., 5, 4) -- SVD-based, robust to B's rank deficiency
    m_or_mt = e_b @ f_b  # (..., 4, 4)

    m = m_or_mt if rotating_role == "analyzer" else np.swapaxes(m_or_mt, -2, -1)

    # Mask the structurally unobservable row/column (README's mode table:
    # 3x4 recovers everything except the bottom row; 4x3 everything except
    # the rightmost column) with NaN rather than pinv's near-zero noise.
    if mode == "3x4":
        m[..., 3, :] = np.nan
    elif mode == "4x3":
        m[..., :, 3] = np.nan
    return m


# ============================================================================
# I/O -- reads measure.py's continuous-mode output for 3x4/4x3
# ============================================================================

def load_continuous_single_arm_run(run_dir: Path) -> dict:
    run_dir = Path(run_dir)
    config = json.loads((run_dir / "Config" / "experiment_config.json").read_text(encoding="utf-8"))
    mode = config["mode"]
    if mode not in MODE_TABLE:
        raise ValueError(f"{run_dir} is mode {mode!r}, not a single-arm continuous mode (3x4/4x3).")
    if config.get("acquisition_type") != "continuous":
        raise ValueError(f"{run_dir} is not a continuous-acquisition run.")

    table = MODE_TABLE[mode]
    outer_angles = [float(v) for v in config.get("outer_angles", [])]
    if not outer_angles:
        raise ValueError(f"{run_dir}'s config has no outer_angles for mode {mode}.")

    rotating_column = "PSG_QWP Angle" if table["rotating_axis"] == "PSG_QWP" else "PSA_QWP Angle"
    log_path = run_dir / "Logs" / "experiment_log.csv"
    rows = []
    with log_path.open("r", newline="", encoding="utf-8-sig") as handle:
        for row in csv.DictReader(handle):
            if row.get("Status") == "SUCCESS":
                rows.append(row)

    from PIL import Image

    dark_path = run_dir / "Results" / "DarkReference.tiff"
    dark = np.asarray(Image.open(dark_path), dtype=np.float64) if dark_path.is_file() else 0.0

    images_dir = run_dir / "Images"
    per_outer_rotating_angles: list = [[] for _ in outer_angles]
    per_outer_intensities: list = [[] for _ in outer_angles]

    # Frames are logged in acquisition order: one full revolution per outer
    # step, in the same order as outer_angles. measure.py's frame_index
    # counts up across the WHOLE session (never resets per revolution --
    # see run_continuous_acquisition), so revolution boundaries must be
    # detected from the rotating angle itself wrapping back near 0 (a
    # large negative jump between consecutive rows), not from frame_index.
    chunks = _split_by_angle_wrap(rows, rotating_column)
    if len(chunks) != len(outer_angles):
        raise ValueError(f"{run_dir}: found {len(chunks)} revolution(s) in the log but {len(outer_angles)} outer_angles.")

    for i, chunk in enumerate(chunks):
        angles = []
        images = []
        for row in chunk:
            angles.append(float(row[rotating_column]))
            frame_index = int(row["Frame Index"])
            matches = list(images_dir.glob(f"frame_{frame_index:04d}_*.tiff"))
            if not matches:
                raise FileNotFoundError(f"No image found for frame index {frame_index} in {images_dir}.")
            image = np.asarray(Image.open(matches[0]), dtype=np.float64) - dark
            images.append(image)
        per_outer_rotating_angles[i] = np.asarray(angles, dtype=np.float64)
        per_outer_intensities[i] = np.stack(images, axis=0)

    return {
        "mode": mode,
        "outer_angles": outer_angles,
        "per_outer_rotating_angles_deg": per_outer_rotating_angles,
        "per_outer_intensities": per_outer_intensities,
    }


def _split_by_angle_wrap(rows: list, angle_column: str) -> list:
    """Splits into per-revolution chunks by detecting the rotating axis's
    logged angle wrapping from near 360 back to near 0 (a large negative
    jump between consecutive rows) -- robust to frame_index never
    resetting and to the odd dropped/failed frame."""

    chunks: list = []
    current: list = []
    previous_angle = None
    for row in rows:
        angle = float(row[angle_column])
        if previous_angle is not None and (angle - previous_angle) < -180.0:
            chunks.append(current)
            current = []
        current.append(row)
        previous_angle = angle
    if current:
        chunks.append(current)
    return chunks


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


def save_reconstruction(run_dir: Path, m: np.ndarray, roi: tuple | None = None) -> Path:
    """Saves the per-pixel M as .npy (height x width x 4 x 4) plus an
    ROI-summarized single M matrix as JSON -- same schema as
    discrete_reconstruction.save_reconstruction."""

    run_dir = Path(run_dir)
    results_dir = run_dir / "Results"
    results_dir.mkdir(parents=True, exist_ok=True)
    np.save(results_dir / "mueller_matrix.npy", m)

    summary: dict = {}
    if m.ndim == 2:
        summary["M"] = m.tolist()
    else:
        height, width = m.shape[:2]
        x, y, w, h = roi if roi is not None else (0, 0, width, height)
        region = m[y : y + h, x : x + w]
        summary["roi"] = {"x": x, "y": y, "width": w, "height": h}
        summary["M"] = np.nanmedian(region, axis=(0, 1)).tolist()

    import json

    out_path = results_dir / "mueller_matrix_summary.json"
    out_path.write_text(json.dumps(summary, indent=2), encoding="utf-8")
    return out_path


# ============================================================================
# Entry point
# ============================================================================

def _parse_args():
    import argparse

    parser = argparse.ArgumentParser(description="Hauge (1978) Section IV single-arm continuous Mueller-matrix reconstruction")
    parser.add_argument("run_dir", type=Path, help="measure.py 3x4/4x3 continuous-mode output directory (Data/<run>/)")
    parser.add_argument("--rotating-calibration-dir", type=Path, required=True, help="qwp_calibration.py output dir calibrating the rotating QWP")
    parser.add_argument("--scalar-calibration", action="store_true", help="Use the ROI-summary s/f/r instead of per-pixel maps")
    parser.add_argument("--roi", type=int, nargs=4, metavar=("X", "Y", "W", "H"), default=None)
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    data = load_continuous_single_arm_run(args.run_dir)
    mode = data["mode"]
    rotating_axis = MODE_TABLE[mode]["rotating_axis"]
    rotating_cal = load_qwp_calibration(args.rotating_calibration_dir, rotating_axis, not args.scalar_calibration)

    m = reconstruct_single_arm(
        mode, data["outer_angles"], data["per_outer_rotating_angles_deg"], data["per_outer_intensities"], rotating_cal, None
    )
    m_norm, _, _ = normalize_mueller_matrix(m)
    out_path = save_reconstruction(args.run_dir, m_norm, tuple(args.roi) if args.roi else None)
    print(f"Mode: {mode}")
    print(f"Saved: {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
