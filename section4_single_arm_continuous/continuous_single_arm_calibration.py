"""continuous_single_arm_calibration.py -- Hauge (1978) Section IV.C,
built-in calibration for the 3x4/4x3 single-arm continuous reconstruction
(continuous_single_arm_reconstruction.py). A SEPARATE run from the real
measurement: same continuous-mode acquisition, system/sample absent.

Pure NumPy, no hardware dependency.

Steps (Hauge Sec. IV.C, adapted to this project's rig -- see
continuous_single_arm_reconstruction.py's module docstring for why the
FIXED companion linear element must sit at its calibrated optical 0):

1. Phase offset C1' (Eq. 57): sweep the rotating compensator through a full
   revolution at the fixed-side reference (fixed companion axis at optical
   0; outer axis also at optical 0), no sample. Theory requires B2=B4=0
   exactly in this configuration. Fit the RAW (phase-uncorrected, "primed")
   Fourier coefficients directly against the logged angles (no assumption
   about where the revolution "started"), then:
       tan(4*C1') = -B4'/A4'
   Either solve this once per session, or physically zero the compensator
   mount so C1'=0 and skip the correction.

2. Rotating side's own s', f' (Eq. 58), using the phase-corrected
   coefficients from step 1:
       s' = A2/(A0+A4),  f' = 2*A4/(A0+A4)

3. Non-rotating (outer) side's s, f -- cross-check against Part 1's
   Section II calibration of that axis (Eq. 59): with the system absent,
   theory requires E == B exactly. Read off independent estimates of the
   outer side's s, f directly from E's entries (matching B's own harmonic
   column layout: 0=const, 1=cos2, 2=sin2, 3=cos4, 4=sin4). For 3x4/4x3
   this mostly confirms E is close to the trivial plain-polarizer B (s=0),
   since neither mode has a real compensator on the outer side -- kept
   general for a hypothetical mode that does.

4. Source response (Eq. 60/61): gIp = 2*[mean(A0) - mean(A4)*(1-f')/f'],
   averaged over the outer-axis sweep.

Calibration module outputs are in the SAME {s,f,r,delta_deg,T} schema as
Part 1's qwp_calibration.py, to support an explicit cross-check report
(agreement = confidence in calibration; disagreement = flag for
investigation) -- see cross_check_against_part1().
"""

from __future__ import annotations

import math
from pathlib import Path

import numpy as np

from continuous_single_arm_reconstruction import fit_outer_fourier, fit_revolution_fourier, harmonic_matrix_polarizer


def measure_phase_offset(angles_deg, intensities: np.ndarray) -> float:
    """Eq. (57): tan(4*C1') = -B4'/A4', from the RAW (uncorrected) per-
    revolution Fourier fit at the fixed-side reference, system absent.
    Returns C1' in degrees (principal branch; the true phase is only
    determined mod 90 deg -- Eq. 57's own tan(4*C1') ambiguity -- same
    fast/slow-axis-style branch caveat as Part 1's null search).

    Always returns a single scalar, even when intensities is a genuine
    per-pixel (n_frames, H, W) stack (fit_revolution_fourier then returns
    per-pixel A4/B4 maps): C1' is a property of the rotating stage's own
    mechanical/encoder zero, not a spatially-varying optical quantity, so
    reducing the per-pixel estimate via nanmedian is physically correct
    here, not a workaround -- and it must be scalar regardless, since
    correct_revolution_angles (below) adds it directly to the shared,
    frame-indexed angle axis that fit_revolution_fourier's single design
    matrix is built from; a per-pixel correction would require a
    per-pixel design matrix, defeating that vectorization entirely.
    Found while building the calibration CLI (MMIE_ATOMIC_TARGETS.md
    Category 6): a real measure.py session's full-frame intensities
    crashed the previous direct float() cast."""

    fourier = fit_revolution_fourier(angles_deg, np.asarray(intensities, dtype=np.float64))
    a4, b4 = fourier["A4"], fourier["B4"]
    phi_deg = np.degrees(np.arctan2(-b4, a4)) / 4.0
    return float(np.nanmedian(phi_deg))


def correct_revolution_angles(angles_deg, c1_prime_deg: float) -> np.ndarray:
    """Shifts logged angles into the phase-corrected reference (Eq. 55's
    role, but applied directly to the angle axis rather than algebraically
    to the primed coefficients -- equivalent, and simpler given we already
    re-fit from raw logged angles rather than an assumed uniform grid).
    Hauge's own convention (Eq. 53/57): the raw/logged azimuth is the TRUE
    azimuth minus C1' (raw = true - C1'), so recovering true from raw is
    an ADDITION, matching tan(4*C1') = -B4'/A4''s sign exactly."""

    return np.asarray(angles_deg, dtype=np.float64) + c1_prime_deg


def measure_rotating_side_defects(fourier: dict) -> dict:
    """Eq. (58): s' = A2/(A0+A4), f' = 2*A4/(A0+A4), from the PHASE-
    CORRECTED per-revolution Fourier fit, system absent. r' follows from
    the p^2+r^2+s^2=1 identity, matching Part 1's compute_calibration."""

    a0, a2, a4 = fourier["A0"], fourier["A2"], fourier["A4"]
    denom = a0 + a4
    s_prime = a2 / denom
    f_prime = 2.0 * a4 / denom
    p_prime = 1.0 - 2.0 * f_prime
    radicand = 1.0 - p_prime**2 - s_prime**2
    r_prime = np.sqrt(np.clip(radicand, 0.0, None))
    delta_deg = np.degrees(np.arctan2(r_prime, p_prime))
    s_clipped = np.clip(s_prime, -1.0, 1.0)
    transmission_t = np.tan(0.5 * np.arccos(s_clipped))
    return {"s": s_prime, "f": f_prime, "p": p_prime, "r": r_prime, "delta_deg": delta_deg, "T": transmission_t}


def measure_non_rotating_side_defects(e_mat: np.ndarray) -> dict:
    """Eq. (59) cross-check: with the system absent, theory requires
    E == B exactly (Eq. 49 with M=I). Reads off two independent estimates
    of the outer side's own s, f directly from E's entries (E's column
    layout matches B's: 0=const, 1=cos2, 2=sin2, 3=cos4, 4=sin4). For
    3x4/4x3 this mostly just confirms E is close to the trivial plain-
    polarizer B (harmonic_matrix_polarizer(): s=0, no cos4/sin4 content),
    since neither mode has a real compensator on the outer side.

    Each estimate is reduced to a single scalar (nanmedian, matching
    measure_phase_offset's reasoning above) even when e_mat is a genuine
    per-pixel (4, 5, H, W) map: these are cross-check point estimates
    against a known-trivial B (s=0 exactly, in theory, everywhere), not a
    physically spatially-varying quantity for these two modes -- found
    while building the calibration CLI, same crash class as
    measure_phase_offset's previous direct float() cast."""

    e_arr = np.asarray(e_mat)
    s_estimates = {"E[0,2]": float(np.nanmedian(e_arr[0, 2])), "E[1,2]": float(np.nanmedian(e_arr[1, 2]))}
    f_estimates = {"E[1,4]": float(np.nanmedian(e_arr[1, 4])), "1-E[1,0]": float(np.nanmedian(1.0 - e_arr[1, 0]))}

    # Same reshape-then-broadcast pattern as reconstruct_single_arm's own
    # b_mat handling: expected_b's (4, 5) shape does not align against
    # e_arr's trailing (H, W) pixel dims without explicit reshaping first
    # (found the same way, while building the calibration CLI -- a naive
    # e_arr - expected_b either raises or, worse, broadcasts against the
    # wrong axes if H or W happens to coincide with 4 or 5).
    expected_b = harmonic_matrix_polarizer()
    pixel_shape = e_arr.shape[2:]
    if pixel_shape:
        expected_b = expected_b.reshape(expected_b.shape + (1,) * len(pixel_shape))
    max_deviation = float(np.max(np.abs(e_arr - expected_b)))
    return {"s_estimates": s_estimates, "f_estimates": f_estimates, "max_deviation_from_trivial_B": max_deviation}


def measure_source_response(fourier_list: list, f_prime) -> float:
    """Eq. (60)/(61): gIp = 2*[<A0> - <A4>*(1-f')/f'], averaged over the
    outer-axis sweep."""

    mean_a0 = float(np.mean([np.asarray(f["A0"]) for f in fourier_list]))
    mean_a4 = float(np.mean([np.asarray(f["A4"]) for f in fourier_list]))
    f_prime_scalar = float(np.mean(f_prime))
    return 2.0 * (mean_a0 - mean_a4 * (1.0 - f_prime_scalar) / f_prime_scalar)


def run_single_arm_calibration(
    mode: str,
    phase_ref_angles_deg,
    phase_ref_intensities: np.ndarray,
    outer_angles_deg,
    per_outer_rotating_angles_deg,
    per_outer_intensities,
) -> dict:
    """Full Sec. IV.C sequence, system/sample absent throughout:

    1-2. C1' and the rotating side's own s',f' both come from the SAME
    dedicated phase-reference revolution (fixed-side + outer axis both at
    optical 0) -- Eq. (57)/(58) are only valid at that specific reference
    configuration, not at an arbitrary outer angle, so they must not be
    averaged in from the general outer-angle sweep.

    3-4. The outer-angle sweep (same shape as a real measurement session,
    sample removed) is then phase-corrected with that SAME C1' and reduced
    to V0..V3 per step using the phase-reference's s',f',r' (mirroring
    continuous_single_arm_reconstruction.recover_rotating_side_vector
    exactly), giving E (cross-checked against the known B) and the source
    response.

    rotating_defects (s,f,p,r,delta_deg,T) stay genuinely per-pixel when
    phase_ref_intensities/per_outer_intensities are full (n_frames, H, W)
    frames -- real, spatially-varying optical quantities, unlike C1'
    above."""

    from continuous_single_arm_reconstruction import MODE_TABLE, recover_rotating_side_vector

    rotating_role = MODE_TABLE[mode]["rotating_role"]

    c1_prime_deg = measure_phase_offset(phase_ref_angles_deg, phase_ref_intensities)
    phase_ref_corrected = correct_revolution_angles(phase_ref_angles_deg, c1_prime_deg)
    phase_ref_fourier = fit_revolution_fourier(phase_ref_corrected, np.asarray(phase_ref_intensities, dtype=np.float64))
    rotating_defects = measure_rotating_side_defects(phase_ref_fourier)

    fourier_list = []
    v_stack = []
    for angles, intensities in zip(per_outer_rotating_angles_deg, per_outer_intensities):
        corrected_angles = correct_revolution_angles(angles, c1_prime_deg)
        fourier = fit_revolution_fourier(corrected_angles, np.asarray(intensities, dtype=np.float64))
        fourier_list.append(fourier)
        v_stack.append(
            recover_rotating_side_vector(
                fourier, rotating_defects["s"], rotating_defects["f"], rotating_defects["r"], rotating_role
            )
        )
    stacked_v = np.stack(v_stack, axis=0)
    # num_harmonics=3: this calibration module only ever cross-checks
    # against the plain-polarizer B (measure_non_rotating_side_defects
    # assumes harmonic_matrix_polarizer() throughout), so the reduced
    # model is always the analytically-correct one here -- see
    # fit_outer_fourier's docstring for why the full 5-parameter model
    # would silently be underdetermined with few outer angles.
    e_mat = fit_outer_fourier(outer_angles_deg, stacked_v, num_harmonics=3)
    outer_cross_check = measure_non_rotating_side_defects(e_mat)

    g_ip = measure_source_response(fourier_list, rotating_defects["f"])

    return {
        "c1_prime_deg": c1_prime_deg,
        "rotating_side": rotating_defects,
        "outer_side_cross_check": outer_cross_check,
        "source_response_g_ip": g_ip,
    }


def cross_check_against_part1(single_arm_result: dict, part1_summary: dict, aggregation: str) -> dict:
    """Compares this module's recovered rotating-side s,f,r,delta_deg,T
    against Part 1's qwp_calibration.py summary for the SAME QWP.
    Agreement (small differences) is confidence in both calibrations;
    disagreement flags something to investigate (alignment drift,
    a bad null, camera nonlinearity)."""

    diffs = {}
    for key in ("s", "f", "r", "delta_deg", "T"):
        this_value = float(np.asarray(single_arm_result["rotating_side"][key]))
        part1_value = float(part1_summary[key][aggregation])
        diffs[key] = {"continuous": this_value, "part1_discrete": part1_value, "diff": this_value - part1_value}
    return diffs


# ============================================================================
# I/O -- reads a real, sample-absent measure.py 3x4/4x3 continuous-mode
# session and drives run_single_arm_calibration() from it (MMIE_ATOMIC_TARGETS.md
# Category 6 -- previously this module was library-only, exercised only by
# test_continuous_single_arm.py's synthetic arrays).
# ============================================================================

def find_zero_outer_step(outer_angles_deg, tolerance_deg: float = 1e-6) -> int:
    """Index of the outer_angles_deg entry at (or within tolerance of) 0
    deg. The phase-reference revolution Eq. (57)/(58) need (fixed-side +
    outer axis both at optical 0) is the SAME physical configuration as
    the outer-sweep's own 0-deg step -- not a coincidence, Hauge's own
    reference config IS "outer axis at 0". So a real sample-absent
    session's outer=0 capture can serve directly as that phase reference;
    no separate dedicated revolution needs to be captured. Raises
    ValueError if no outer step is at 0 deg -- measure.py's own
    OUTER_ANGLES_DEG default ([0, 45, 90]) satisfies this, but a custom
    config that omits 0 deg does not, and there is no way to recover the
    phase reference from this session in that case."""

    matches = [i for i, angle in enumerate(outer_angles_deg) if abs(float(angle)) <= tolerance_deg]
    if not matches:
        raise ValueError(
            "No outer_angles_deg entry at 0 deg in this session -- the phase-reference "
            "revolution (Eq. 57/58) requires the outer axis at its calibrated optical 0. "
            f"Got outer_angles_deg={list(outer_angles_deg)!r}. Re-run measure.py's "
            "calibration session with 0.0 included in OUTER_ANGLES_DEG."
        )
    return matches[0]


def load_and_run_single_arm_calibration(run_dir) -> dict:
    """Reads a sample-absent measure.py 3x4/4x3 continuous-mode session
    (via continuous_single_arm_reconstruction.load_continuous_single_arm_run,
    the same loader the reconstruction CLI uses for a real sample) and runs
    the full Sec. IV.C sequence above, reusing the outer=0 step's own
    revolution as the phase reference (find_zero_outer_step, above) rather
    than requiring a second, separately-captured revolution."""

    from continuous_single_arm_reconstruction import load_continuous_single_arm_run

    data = load_continuous_single_arm_run(run_dir)
    zero_index = find_zero_outer_step(data["outer_angles"])
    result = run_single_arm_calibration(
        data["mode"],
        data["per_outer_rotating_angles_deg"][zero_index],
        data["per_outer_intensities"][zero_index],
        data["outer_angles"],
        data["per_outer_rotating_angles_deg"],
        data["per_outer_intensities"],
    )
    result["mode"] = data["mode"]
    return result


def _reduce_to_scalar(value, roi, aggregation: str) -> float:
    """Reduces a per-pixel (H, W) result to one number via an ROI median/
    mean -- matching qwp_calibration.summarize_roi's aggregation choice. A
    genuinely scalar value (e.g. this module fed ROI-mean-reduced rather
    than per-pixel intensities upstream) is returned unchanged."""

    arr = np.asarray(value, dtype=np.float64)
    if arr.ndim < 2:
        return float(arr)
    stat = np.nanmedian if aggregation == "median" else np.nanmean
    x, y, w, h = roi
    return float(stat(arr[y : y + h, x : x + w]))


def summarize_rotating_side(rotating_side: dict, roi, aggregation: str) -> dict:
    """Reduces run_single_arm_calibration()'s per-pixel rotating_side dict
    to a flat {field: float} dict via the ROI -- the shape both printing
    and cross_check_against_part1() need (that function reads each field
    directly as a scalar, not nested under an aggregation key)."""

    return {key: _reduce_to_scalar(value, roi, aggregation) for key, value in rotating_side.items()}


def _infer_default_roi(result: dict):
    """Whole-frame ROI derived from the recovered s-map's own shape, or
    None if the result is already scalar (non-per-pixel) end to end."""

    s_array = np.asarray(result["rotating_side"]["s"])
    if s_array.ndim < 2:
        return None
    height, width = s_array.shape[-2:]
    return (0, 0, width, height)


# ============================================================================
# Entry point
# ============================================================================

def _parse_args():
    import argparse

    parser = argparse.ArgumentParser(
        description="Hauge (1978) Section IV.C built-in calibration + cross-check against Section II"
    )
    parser.add_argument(
        "run_dir", type=Path,
        help="measure.py 3x4/4x3 continuous-mode, SAMPLE-ABSENT session directory (Data/<run>/); "
             "its OUTER_ANGLES_DEG must include 0.0",
    )
    parser.add_argument(
        "--compare-to", type=Path, default=None, metavar="CALIBRATION_RESULT_JSON",
        help="Section II qwp_calibration.py's Config/calibration_result.json, to cross-check against",
    )
    parser.add_argument(
        "--aggregation", choices=("mean", "median"), default="median",
        help="ROI aggregation for this module's own printed/saved summary (matches "
             "qwp_calibration.py's AGGREGATION default of 'median'); independent of whichever "
             "aggregation --compare-to's own file was saved with",
    )
    parser.add_argument("--roi", type=int, nargs=4, metavar=("X", "Y", "W", "H"), default=None)
    return parser.parse_args()


def main() -> int:
    import json

    from continuous_single_arm_reconstruction import MODE_TABLE

    args = _parse_args()
    result = load_and_run_single_arm_calibration(args.run_dir)
    mode = result["mode"]
    rotating_axis = MODE_TABLE[mode]["rotating_axis"]
    roi = tuple(args.roi) if args.roi else _infer_default_roi(result)

    rotating_summary = summarize_rotating_side(result["rotating_side"], roi, args.aggregation)
    c1_prime_deg = _reduce_to_scalar(result["c1_prime_deg"], roi, args.aggregation)
    g_ip = _reduce_to_scalar(result["source_response_g_ip"], roi, args.aggregation)
    max_deviation = _reduce_to_scalar(
        result["outer_side_cross_check"]["max_deviation_from_trivial_B"], roi, args.aggregation
    )

    print(f"Mode: {mode} (rotating axis: {rotating_axis})")
    print(f"C1' (phase offset): {c1_prime_deg:.5f} deg")
    print(f"Source response g*Ip: {g_ip:.6f}")
    print(f"Outer-side cross-check max deviation from trivial B: {max_deviation:.3e}")
    print(f"\n--- {rotating_axis} recovered defect parameters (continuous, Sec. IV.C) ---")
    for key in ("s", "f", "r", "delta_deg", "T"):
        print(f"  {key:10s} = {rotating_summary[key]:.5f}")

    report: dict = {
        "mode": mode,
        "rotating_axis": rotating_axis,
        "aggregation": args.aggregation,
        "roi": {"x": roi[0], "y": roi[1], "width": roi[2], "height": roi[3]} if roi else None,
        "c1_prime_deg": c1_prime_deg,
        "source_response_g_ip": g_ip,
        "outer_side_cross_check_max_deviation": max_deviation,
        "rotating_side": rotating_summary,
    }

    if args.compare_to is not None:
        part1_report = json.loads(args.compare_to.read_text(encoding="utf-8"))
        available = list(part1_report.get("results", {}))
        if rotating_axis not in part1_report.get("results", {}):
            raise ValueError(f"{args.compare_to} has no calibration results for {rotating_axis!r} (found: {available}).")
        part1_summary = part1_report["results"][rotating_axis]["summary"]
        part1_aggregation = part1_report["aggregation"]
        diffs = cross_check_against_part1({"rotating_side": rotating_summary}, part1_summary, part1_aggregation)
        report["cross_check_against_part1"] = diffs
        print(f"\n--- Cross-check against Section II ({args.compare_to}, aggregation={part1_aggregation!r}) ---")
        for field, values in diffs.items():
            print(
                f"  {field:10s} continuous={values['continuous']:.5f}  "
                f"part1_discrete={values['part1_discrete']:.5f}  diff={values['diff']:+.5f}"
            )

    results_dir = Path(args.run_dir) / "Results"
    results_dir.mkdir(parents=True, exist_ok=True)
    out_path = results_dir / "single_arm_calibration_cross_check.json"
    out_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(f"\nSaved: {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
