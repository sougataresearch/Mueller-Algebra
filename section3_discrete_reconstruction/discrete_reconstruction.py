"""discrete_reconstruction.py -- Hauge (1978) Section III, generalized to an
arbitrary angle grid: Mueller-matrix reconstruction from measure.py's
discrete-mode output.

Pure NumPy, no hardware dependency. Reads exactly the on-disk layout
documented in README.md and written by measure.py (not modified by this
file): Config/experiment_config.json ("mode", "fixed_angles",
"state_inputs"), Images/<first>_<second>.tiff, Results/DarkReference.tiff.
Mode table (which axis is "first"/"second", which side carries a QWP) is
duplicated here (not imported from measure.py) per this project's existing
"each consumer owns its own physics copy" convention -- see README.md's own
copy of the same table.

Physics -- Hauge Eq. (8)/(11), generalized beyond Hauge's own worked
examples (III.A/III.C, which only cover 4 fixed azimuth combinations) to an
arbitrary angle grid on either/both sides:

  Generator (PSG side, azimuths P, C):
    S0 = 1 + s*cos(2C-2P)
    S1 = f*cos(4C-2P) + s*cos(2C) + (1-f)*cos(2P)
    S2 = f*sin(4C-2P) + s*sin(2C) + (1-f)*sin(2P)
    S3 = r*sin(2C-2P)

  Analyzer (PSD side, azimuths C', A) -- the mathematical dual, with a
  sign flip on the r' term (Hauge Eq. 11):
    d0 = 1 + s'*cos(2C'-2A)
    d1 = f'*cos(4C'-2A) + s'*cos(2C') + (1-f')*cos(2A)
    d2 = f'*sin(4C'-2A) + s'*sin(2C') + (1-f')*sin(2A)
    d3 = -r'*sin(2C'-2A)

For 3x3/4x3 (no QWP on PSG) or 3x3/3x4 (no QWP on PSA), that side collapses
to the plain polarizer column/row {1, cos(2X), sin(2X), 0} (P and A are
assumed perfect polarizers throughout, per Hauge Sec. II.A).

Forward model (Eq. 17): R = (g*Ip/2) * D @ M @ G, where G's columns are
generator vectors (one per commanded (P,C) state) and D's rows are
analyzer vectors (one per commanded (C',A) state).

Solve:
  - Exactly 4 states/side: direct inverse, Eq. (18):
    M = (2/(g*Ip)) * inv(D) @ R @ inv(G)
  - Overdetermined (typical): joint least squares over the vectorized
    system (more numerically robust than separately pseudo-inverting each
    side), via the normal equations solved batched per pixel with
    numpy.linalg.solve's leading-dimension broadcasting (no per-pixel
    Python loop).

The arbitrary (2/g*Ip) scale factor is never separately estimated: it is
irrelevant after the required final M00-normalization (Hauge's own
"epsilon=0/delta=90 reduction always gives 2*identity unnormalized, not
identity" caveat), so every solve below computes M only up to that scale
and normalize_mueller_matrix() removes it in one place.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np

# ============================================================================
# Mode table -- duplicated from README.md / measure.py's MODE_DEFINITIONS,
# deliberately (this project's "each consumer owns its own physics copy"
# convention). Do not hardcode a different convention here.
# ============================================================================

MODE_TABLE = {
    "3x3": {"first_axis": "PSG_Polarizer", "second_axis": "PSA_Analyzer", "psg_has_qwp": False, "psa_has_qwp": False},
    "3x4": {"first_axis": "PSG_QWP", "second_axis": "PSA_Analyzer", "psg_has_qwp": True, "psa_has_qwp": False},
    "4x3": {"first_axis": "PSG_Polarizer", "second_axis": "PSA_QWP", "psg_has_qwp": False, "psa_has_qwp": True},
    "4x4": {"first_axis": "PSG_QWP", "second_axis": "PSA_QWP", "psg_has_qwp": True, "psa_has_qwp": True},
}


def _angle_text(angle: float) -> str:
    """Matches measure.py's angle_text() filename formatting exactly."""

    return f"{float(angle):g}"


# ============================================================================
# Generator / analyzer vectors -- Hauge Eq. (8)/(11)
# ============================================================================

def _stack4(a, b, c, d) -> np.ndarray:
    """Stacks four scalar-or-per-pixel-map components into shape (4, ...),
    broadcasting scalars against any per-pixel (H, W) maps present."""

    a, b, c, d = np.broadcast_arrays(
        np.asarray(a, dtype=np.float64), np.asarray(b, dtype=np.float64),
        np.asarray(c, dtype=np.float64), np.asarray(d, dtype=np.float64),
    )
    return np.stack([a, b, c, d], axis=0)


def generator_vector_qwp(c_deg, p_deg: float, s, f, r) -> np.ndarray:
    """Hauge Eq. (8): PSG side with a QWP at azimuth C, fixed polarizer at
    azimuth P. s, f, r may be scalars (ROI-summary calibration) or (H, W)
    per-pixel maps (Part 1's preferred calibration form)."""

    c = np.deg2rad(c_deg)
    p = np.deg2rad(p_deg)
    s0 = 1.0 + s * np.cos(2 * c - 2 * p)
    s1 = f * np.cos(4 * c - 2 * p) + s * np.cos(2 * c) + (1.0 - f) * np.cos(2 * p)
    s2 = f * np.sin(4 * c - 2 * p) + s * np.sin(2 * c) + (1.0 - f) * np.sin(2 * p)
    s3 = r * np.sin(2 * c - 2 * p)
    return _stack4(s0, s1, s2, s3)


def generator_vector_polarizer(p_deg) -> np.ndarray:
    """No QWP on the PSG side: plain (ideal) polarizer column, P itself the
    varying angle."""

    p = np.deg2rad(p_deg)
    return _stack4(1.0, np.cos(2 * p), np.sin(2 * p), 0.0)


def analyzer_vector_qwp(cp_deg, a_deg: float, s, f, r) -> np.ndarray:
    """Hauge Eq. (11): PSD side with a QWP at azimuth C', fixed analyzer at
    azimuth A. Mathematical dual of generator_vector_qwp, with the sign
    flip on the r term."""

    cp = np.deg2rad(cp_deg)
    a = np.deg2rad(a_deg)
    d0 = 1.0 + s * np.cos(2 * cp - 2 * a)
    d1 = f * np.cos(4 * cp - 2 * a) + s * np.cos(2 * cp) + (1.0 - f) * np.cos(2 * a)
    d2 = f * np.sin(4 * cp - 2 * a) + s * np.sin(2 * cp) + (1.0 - f) * np.sin(2 * a)
    d3 = -r * np.sin(2 * cp - 2 * a)
    return _stack4(d0, d1, d2, d3)


def analyzer_vector_polarizer(a_deg) -> np.ndarray:
    """No QWP on the PSD side: plain (ideal) analyzer row, A itself the
    varying angle."""

    a = np.deg2rad(a_deg)
    return _stack4(1.0, np.cos(2 * a), np.sin(2 * a), 0.0)


def make_generator_vector(mode: str, first_angle_deg: float, fixed_angles: dict, psg_cal: dict | None) -> np.ndarray:
    table = MODE_TABLE[mode]
    if table["psg_has_qwp"]:
        return generator_vector_qwp(first_angle_deg, fixed_angles["PSG_Polarizer"], psg_cal["s"], psg_cal["f"], psg_cal["r"])
    return generator_vector_polarizer(first_angle_deg)


def make_analyzer_vector(mode: str, second_angle_deg: float, fixed_angles: dict, psa_cal: dict | None) -> np.ndarray:
    table = MODE_TABLE[mode]
    if table["psa_has_qwp"]:
        return analyzer_vector_qwp(second_angle_deg, fixed_angles["PSA_Analyzer"], psa_cal["s"], psa_cal["f"], psa_cal["r"])
    return analyzer_vector_polarizer(second_angle_deg)


# ============================================================================
# Forward model + solve -- Hauge Eq. (17)/(18), vectorized (no per-pixel
# Python loop): every pixel's small (4x4-unknown) linear system is solved
# at once via numpy's leading-dimension-broadcasting linalg.inv/solve.
# ============================================================================

def _solve_exact(d_mat: np.ndarray, g_mat: np.ndarray, r_mat: np.ndarray) -> np.ndarray:
    """Eq. (18), up to the arbitrary (2/g*Ip) scale (removed later by
    M00-normalization). d_mat, g_mat, r_mat: shape (..., 4, 4)."""

    d_inv = np.linalg.inv(d_mat)
    g_inv = np.linalg.inv(g_mat)
    return d_inv @ r_mat @ g_inv


def _solve_lstsq(d_mat: np.ndarray, g_mat: np.ndarray, r_mat: np.ndarray) -> np.ndarray:
    """Joint least-squares solve of vec(R) = kron(G^T, D) @ vec(M) (Part 2
    spec), implemented directly from the physical equation
    R_ij = d_i . M . s_j per state pair (i, j) -- equivalent, and avoids
    needing to match vec()'s column-major convention explicitly.
    d_mat: shape (..., n_second, 4); g_mat: shape (..., 4, n_first);
    r_mat: shape (..., n_second, n_first). Solved via the batched normal
    equations (numpy.linalg.solve broadcasts over leading "..." pixel
    dims), not a per-pixel Python loop."""

    n_second = d_mat.shape[-2]
    rows_needed = d_mat.shape[-1]
    cols_needed = g_mat.shape[-2]
    n_first = g_mat.shape[-1]
    n_unknowns = rows_needed * cols_needed
    # A[..., i, j, a, b] = d_mat[..., i, a] * g_mat[..., b, j]
    a_full = np.einsum("...ia,...bj->...ijab", d_mat, g_mat)
    batch_shape = a_full.shape[:-4]
    a_flat = a_full.reshape(batch_shape + (n_second * n_first, n_unknowns))
    b_flat = r_mat.reshape(batch_shape + (n_second * n_first,))

    ata = np.einsum("...ka,...kb->...ab", a_flat, a_flat)
    atb = np.einsum("...ka,...k->...a", a_flat, b_flat)
    # NumPy >=2.0 requires an explicit trailing "columns" axis on the rhs to
    # batch-broadcast a vector solve -- a bare (..., M) rhs is no longer
    # auto-detected as a stack of vectors like it was pre-2.0.
    vec_m = np.linalg.solve(ata, atb[..., np.newaxis])[..., 0]
    return vec_m.reshape(batch_shape + (rows_needed, cols_needed))


def _vector_arity(mode: str) -> tuple[int, int]:
    """(rows_needed, cols_needed): a side with no QWP only spans a 3-D
    subspace (its S3/d3 component is identically zero -- a plain polarizer
    never touches circular polarization), so its vectors/matrix must be
    truncated to 3 components, not padded to 4, or the "exact" 4x4 solve
    would try to invert a matrix with an all-zero row/column. Matches
    measure.py's MODE_DEFINITIONS "matrix_shape" exactly: (3,3) for 3x3,
    (3,4) for 3x4, (4,3) for 4x3, (4,4) for 4x4."""

    table = MODE_TABLE[mode]
    cols_needed = 4 if table["psg_has_qwp"] else 3
    rows_needed = 4 if table["psa_has_qwp"] else 3
    return rows_needed, cols_needed


def _ideal_grid_rank(mode: str, angles, fixed_angles: dict) -> int:
    """Same idea as measure.py's own check_angle_grid_rank (duplicated per
    this project's "each consumer owns its own physics copy" convention):
    an ideal-optics (s=0, f=0.5, r=1) proxy check of whether a CANDIDATE
    `angles` list, used as both FIRST_ANGLES_DEG and SECOND_ANGLES_DEG
    (measure.py's own default pattern), would give a full-rank system for
    `mode` -- without needing any real calibration data or captured images
    yet. Used by suggest_angle_grid() below; not a substitute for
    measure.py's own pre-flight check against your ACTUAL chosen grid."""

    ideal_cal = {"s": 0.0, "f": 0.5, "r": 1.0}
    table = MODE_TABLE[mode]
    psg_cal = ideal_cal if table["psg_has_qwp"] else None
    psa_cal = ideal_cal if table["psa_has_qwp"] else None
    rows_needed, cols_needed = _vector_arity(mode)

    rows = []
    for second in angles:
        d = make_analyzer_vector(mode, second, fixed_angles, psa_cal)[:rows_needed]
        for first in angles:
            g = make_generator_vector(mode, first, fixed_angles, psg_cal)[:cols_needed]
            rows.append(np.kron(d, g))
    return int(np.linalg.matrix_rank(np.asarray(rows)))


def suggest_angle_grid(mode: str, num_angles: int, fixed_angles: dict | None = None) -> list[float]:
    """Suggests a full-rank grid of num_angles angles for `mode`'s discrete
    acquisition, meant to be used as BOTH measure.py's FIRST_ANGLES_DEG and
    SECOND_ANGLES_DEG (its own default pattern: one shared list, the
    Cartesian product of it with itself is the actual state grid).

    Section III already reconstructs correctly from any num_angles at or
    above the mode's minimum (rows_needed, cols_needed from _vector_arity)
    -- reconstruct_mueller_matrix dispatches to the exact solve at the
    minimum and least-squares above it automatically; MORE states reduces
    noise on the recovered M the same way it does in every other section
    (see test_discrete_reconstruction's test_more_states_reduce_noise).
    This function only helps pick an angle LIST that won't alias (the same
    "QWP angles spaced 90 deg apart" trap measure.py's own
    check_angle_grid_rank guards against) -- it does not replace that
    pre-flight check against your real, final FIRST_ANGLES_DEG/
    SECOND_ANGLES_DEG choice, which also depends on your actual fixed
    angles and (for the rank check specifically) an assumed retardance.

    Strategy: first try a few "nice" evenly-spaced-across-180-deg
    candidates (readable, easy to type into FIRST_ANGLES_DEG by hand) at
    different phase offsets. A rotating QWP's generator/analyzer vector
    has its own resonances beyond the simple 90-deg-spacing case measure.py
    already guards against -- e.g. 0/45/90/135 (any phase, any of these 4
    evenly-45-deg-spaced points) aliases to rank 9 of the 12 needed for
    3x4, regardless of WHICH 45-deg-spaced set you pick, because 45 deg
    steps are themselves a resonance of the cos(2*theta)/cos(4*theta)
    structure -- so evenly-spaced candidates can exhaust every phase
    offset and still fail. When they do, fall back to a deterministic
    (seeded, reproducible -- same mode+num_angles always suggests the same
    grid) pseudo-random search, which empirically finds a full-rank grid
    on the first or second try even where every evenly-spaced candidate
    failed."""

    fixed_angles = fixed_angles or {}
    rows_needed, cols_needed = _vector_arity(mode)
    minimum_needed = max(rows_needed, cols_needed)
    if num_angles < minimum_needed:
        raise ValueError(f"Mode {mode} needs at least {minimum_needed} angles; got {num_angles}.")
    required_rank = rows_needed * cols_needed

    for attempt in range(num_angles):
        offset_deg = attempt * (180.0 / num_angles) / num_angles
        candidate = [float(a) for a in (np.linspace(0.0, 180.0, num_angles, endpoint=False) + offset_deg)]
        if _ideal_grid_rank(mode, candidate, fixed_angles) >= required_rank:
            return candidate

    # NOTE: NOT Python's built-in hash() -- string hashing is randomized
    # per-process (PYTHONHASHSEED) unless disabled, which would silently
    # break the "same mode+num_angles always suggests the same grid"
    # promise above. A simple ordinal checksum is deterministic everywhere.
    seed = sum(ord(character) for character in mode) * 1000 + num_angles
    rng = np.random.default_rng(seed)
    for _ in range(50):
        candidate = sorted(float(a) for a in rng.uniform(0.0, 180.0, num_angles))
        if _ideal_grid_rank(mode, candidate, fixed_angles) >= required_rank:
            return candidate

    raise RuntimeError(
        f"Could not find a full-rank {num_angles}-angle grid for mode {mode} after 50 search attempts "
        "(unexpected -- please report this)."
    )


def reconstruct_mueller_matrix(
    mode: str,
    first_angles,
    second_angles,
    fixed_angles: dict,
    r: np.ndarray,
    psg_cal: dict | None = None,
    psa_cal: dict | None = None,
) -> np.ndarray:
    """r: measured (dark-subtracted) intensities, shape (n_second, n_first)
    for a scalar-calibration reconstruction, or (n_second, n_first, H, W)
    for a per-pixel one; r[i, j] is the reading at (second_angles[i],
    first_angles[j]) -- matching measure.py's Images/<first>_<second>.tiff
    naming and product(first_angles, second_angles) state ordering.

    Returns M, shape (4, 4) or (H, W, 4, 4) -- UN-normalized (see
    normalize_mueller_matrix). For 3x3/3x4/4x3 modes the entries outside
    that mode's recoverable sub-block (README.md's mode table -- e.g. the
    bottom row for 3x4, the rightmost column for 4x3) are NaN: genuinely
    unobservable with no QWP on that arm, not a solver failure."""

    first_angles = list(first_angles)
    second_angles = list(second_angles)
    n_first = len(first_angles)
    n_second = len(second_angles)
    rows_needed, cols_needed = _vector_arity(mode)
    if n_first < cols_needed or n_second < rows_needed:
        raise ValueError(
            f"Mode {mode} needs at least {cols_needed} first-axis and {rows_needed} "
            f"second-axis states; got {n_first} and {n_second}."
        )

    # Truncate to the recoverable arity -- for a no-QWP side this just
    # drops that side's always-zero S3/d3 component.
    g_vectors = [make_generator_vector(mode, angle, fixed_angles, psg_cal)[:cols_needed] for angle in first_angles]
    d_vectors = [make_analyzer_vector(mode, angle, fixed_angles, psa_cal)[:rows_needed] for angle in second_angles]

    # d_mat: (n_second, rows_needed, ...) -> g_mat: (cols_needed, n_first, ...)
    d_mat = np.stack(d_vectors, axis=0)
    g_mat = np.stack(g_vectors, axis=1)
    # Broadcast d_mat/g_mat's trailing pixel dims (if any) against r's. One
    # side may carry real per-pixel calibration maps while the other (e.g.
    # a no-QWP plain-polarizer side) is scalar -- pad the scalar side with
    # singleton pixel axes first so numpy broadcasts them elementwise
    # rather than trying to align them against pixel_shape from the right.
    pixel_shape = r.shape[2:]
    if pixel_shape:
        if d_mat.shape[2:] != pixel_shape:
            d_mat = d_mat.reshape(d_mat.shape[:2] + (1,) * len(pixel_shape))
        d_mat = np.broadcast_to(d_mat, (n_second, rows_needed) + pixel_shape).copy()
        if g_mat.shape[2:] != pixel_shape:
            g_mat = g_mat.reshape(g_mat.shape[:2] + (1,) * len(pixel_shape))
        g_mat = np.broadcast_to(g_mat, (cols_needed, n_first) + pixel_shape).copy()

    # Move the (n_second/rows_needed, cols_needed/n_first) matrix axes to
    # the end so any per-pixel dims become the batch dims numpy's linalg
    # broadcasts over.
    d_b = np.moveaxis(d_mat, [0, 1], [-2, -1])
    g_b = np.moveaxis(g_mat, [0, 1], [-2, -1])
    r_b = np.moveaxis(r, [0, 1], [-2, -1])

    if n_first == cols_needed and n_second == rows_needed:
        m_sub = _solve_exact(d_b, g_b, r_b)
    else:
        m_sub = _solve_lstsq(d_b, g_b, r_b)

    if rows_needed == 4 and cols_needed == 4:
        return m_sub
    full = np.full(pixel_shape + (4, 4), np.nan, dtype=np.float64)
    full[..., :rows_needed, :cols_needed] = m_sub
    return full


def normalize_mueller_matrix(m: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Normalize by M00 (Hauge's ideal-QWP reduction gives 2*identity
    unnormalized, not identity -- M00 is never assumed to be 1 before this)
    and compute the physical-realizability diagnostic trace(M^T M) <=
    4*M00^2 (checked on the normalized matrix, where M00=1, i.e.
    trace(M^T M) <= 4). Returns (M_normalized, trace_mtm, is_realizable)."""

    m00 = m[..., 0, 0]
    m00_safe = np.where(m00 == 0, np.nan, m00)
    m_norm = m / m00_safe[..., np.newaxis, np.newaxis]
    trace_mtm = np.einsum("...ab,...ab->...", m_norm, m_norm)
    is_realizable = trace_mtm <= 4.0 * m_norm[..., 0, 0] ** 2 + 1e-9
    return m_norm, trace_mtm, is_realizable


# ============================================================================
# I/O -- reads measure.py's discrete-mode output (README.md schema) and
# qwp_calibration.py's calibration_result.json (unchanged from Part 1)
# ============================================================================

def load_discrete_run(run_dir: Path) -> dict:
    run_dir = Path(run_dir)
    config = json.loads((run_dir / "Config" / "experiment_config.json").read_text(encoding="utf-8"))
    mode = config["mode"]
    if mode not in MODE_TABLE:
        raise ValueError(f"Unknown mode {mode!r} in {run_dir}.")
    if config.get("acquisition_type") != "discrete":
        raise ValueError(f"{run_dir} is not a discrete-acquisition run (acquisition_type={config.get('acquisition_type')!r}).")

    table = MODE_TABLE[mode]
    fixed_angles = {key: float(value) for key, value in config.get("fixed_angles", {}).items()}
    state_inputs = config["state_inputs"]
    first_angles = [float(v) for v in state_inputs[table["first_axis"]]]
    second_angles = [float(v) for v in state_inputs[table["second_axis"]]]

    from PIL import Image

    dark_path = run_dir / "Results" / "DarkReference.tiff"
    dark = np.asarray(Image.open(dark_path), dtype=np.float64) if dark_path.is_file() else 0.0

    images_dir = run_dir / "Images"
    r = None
    for i, second in enumerate(second_angles):
        for j, first in enumerate(first_angles):
            path = images_dir / f"{_angle_text(first)}_{_angle_text(second)}.tiff"
            if not path.is_file():
                raise FileNotFoundError(f"Missing image {path} for state (first={first}, second={second}).")
            image = np.asarray(Image.open(path), dtype=np.float64) - dark
            if r is None:
                r = np.empty((len(second_angles), len(first_angles)) + image.shape, dtype=np.float64)
            r[i, j] = image

    return {
        "mode": mode,
        "fixed_angles": fixed_angles,
        "first_angles": first_angles,
        "second_angles": second_angles,
        "r": r,
    }


def load_qwp_calibration(calibration_run_dir: Path, target: str, use_per_pixel: bool = True) -> dict:
    """Reads qwp_calibration.py's Config/calibration_result.json (Part 1's
    combined output). use_per_pixel=True (preferred, per Part 2 spec) loads
    the saved .npy maps; False uses the ROI-aggregated scalar summary."""

    run_dir = Path(calibration_run_dir)
    report = json.loads((run_dir / "Config" / "calibration_result.json").read_text(encoding="utf-8"))
    entry = report["results"][target]
    if use_per_pixel:
        return {key: np.load(run_dir / entry["per_pixel_maps"][key]) for key in ("s", "f", "r")}
    summary = entry["summary"]
    aggregation = report["aggregation"]
    return {key: summary[key][aggregation] for key in ("s", "f", "r")}


def reconstruct_from_run(
    run_dir: Path,
    psg_calibration_dir: Path | None = None,
    psa_calibration_dir: Path | None = None,
    use_per_pixel_calibration: bool = True,
) -> dict:
    data = load_discrete_run(run_dir)
    mode = data["mode"]
    table = MODE_TABLE[mode]

    psg_cal = None
    if table["psg_has_qwp"]:
        if psg_calibration_dir is None:
            raise ValueError(f"Mode {mode} needs a PSG_QWP calibration run (--psg-calibration-dir).")
        psg_cal = load_qwp_calibration(psg_calibration_dir, "PSG_QWP", use_per_pixel_calibration)

    psa_cal = None
    if table["psa_has_qwp"]:
        if psa_calibration_dir is None:
            raise ValueError(f"Mode {mode} needs a PSA_QWP calibration run (--psa-calibration-dir).")
        psa_cal = load_qwp_calibration(psa_calibration_dir, "PSA_QWP", use_per_pixel_calibration)

    m = reconstruct_mueller_matrix(
        mode, data["first_angles"], data["second_angles"], data["fixed_angles"], data["r"], psg_cal, psa_cal
    )
    m_norm, trace_mtm, is_realizable = normalize_mueller_matrix(m)
    return {
        "mode": mode,
        "M": m_norm,
        "trace_mtm": trace_mtm,
        "is_realizable": is_realizable,
        "n_first_states": len(data["first_angles"]),
        "n_second_states": len(data["second_angles"]),
    }


def save_reconstruction(run_dir: Path, result: dict, roi: tuple | None = None) -> Path:
    """Saves the per-pixel M as .npy (height x width x 4 x 4) plus an
    ROI-summarized single M matrix as JSON (Part 2 deliverable)."""

    run_dir = Path(run_dir)
    results_dir = run_dir / "Results"
    results_dir.mkdir(parents=True, exist_ok=True)
    m = result["M"]

    np.save(results_dir / "mueller_matrix.npy", m)
    np.save(results_dir / "mueller_matrix_trace_mtm.npy", result["trace_mtm"])

    summary: dict = {"mode": result["mode"], "n_first_states": result["n_first_states"], "n_second_states": result["n_second_states"]}
    if m.ndim == 2:
        summary["M"] = m.tolist()
        summary["trace_mtm"] = float(result["trace_mtm"])
        summary["is_realizable"] = bool(result["is_realizable"])
    else:
        height, width = m.shape[:2]
        if roi is None:
            roi = (0, 0, width, height)
        x, y, w, h = roi
        region = m[y : y + h, x : x + w]
        summary["roi"] = {"x": x, "y": y, "width": w, "height": h}
        summary["M"] = np.nanmedian(region, axis=(0, 1)).tolist()
        summary["trace_mtm"] = float(np.nanmedian(result["trace_mtm"][y : y + h, x : x + w]))
        summary["is_realizable_fraction"] = float(np.mean(result["is_realizable"][y : y + h, x : x + w]))

    out_path = results_dir / "mueller_matrix_summary.json"
    out_path.write_text(json.dumps(summary, indent=2), encoding="utf-8")
    return out_path


# ============================================================================
# Entry point
# ============================================================================

def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Hauge (1978) Section III discrete Mueller-matrix reconstruction")
    parser.add_argument("run_dir", type=Path, help="measure.py discrete-mode output directory (Data/<run>/)")
    parser.add_argument("--psg-calibration-dir", type=Path, default=None, help="qwp_calibration.py output dir calibrating PSG_QWP")
    parser.add_argument("--psa-calibration-dir", type=Path, default=None, help="qwp_calibration.py output dir calibrating PSA_QWP")
    parser.add_argument("--scalar-calibration", action="store_true", help="Use the ROI-summary s/f/r instead of per-pixel maps")
    parser.add_argument("--roi", type=int, nargs=4, metavar=("X", "Y", "W", "H"), default=None)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    result = reconstruct_from_run(
        args.run_dir, args.psg_calibration_dir, args.psa_calibration_dir, not args.scalar_calibration
    )
    out_path = save_reconstruction(args.run_dir, result, tuple(args.roi) if args.roi else None)
    print(f"Mode: {result['mode']} ({result['n_second_states']}x{result['n_first_states']} states)")
    print(f"Saved: {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
