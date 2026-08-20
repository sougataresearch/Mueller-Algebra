"""continuous_dual_arm_calibration.py -- Hauge (1978) Section V.B, built-in
calibration for the 4x4 dual-rotating-compensator reconstruction
(continuous_dual_arm_reconstruction.py). A SEPARATE run, same 4x4
continuous acquisition, system/sample absent.

Pure NumPy, no hardware dependency.

With M=I (system absent), Eq. (72)/(73) show only the EVEN coefficients up
to the 10th harmonic have appreciable size, and ALL b_j are exactly zero
(a strong built-in sanity check -- real air data with a large b_j
indicates misalignment):
    a0 = 1+(1-f)(1-f')                  (~1.25 for near-ideal QWPs)
    a2 = f*(1-f')                       (~0.25)
    a4 = (3*s*s' - r*r')/2              (~-0.5)
    a6 = (s*s' + r*r')/2                (~0.5)
    a8 = f*f'                           (~0.25)
    a10 = f'*(1-f)                      (~0.25)

The largest harmonics (4th, 6th) fix the unknown phase origins (Eq. 75):
    C1  = (phi6 - phi4)/4
    C1' = (phi6 + phi4)/4
with consistency checks phi4=(phi10-phi2)/2, phi6=(phi10+phi2)/2,
phi8=phi10-phi2 (Eq. 76) -- and, once C1, C1' are known, EVERY phi_j
follows directly from the same theta_j(C, C') formulas
(continuous_dual_arm_reconstruction._theta) evaluated at (C1, C1')
(Eq. 69) -- there is nothing left "unmeasurable" once phi4, phi6 are
known, so this module recovers all 12 and phase-corrects every raw
(primed) coefficient via Eq. (77):
    Aj = Aj'*cos(phi_j) - Bj'*sin(phi_j)
    Bj = Bj'*cos(phi_j) + Aj'*sin(phi_j)

Defect parameters and source response follow from the phase-corrected
coefficients (Eq. 78/79):
    g*Ip = 2*(A8+A10)*(A8+A2)/A8
    f  = A8/(A8+A10)
    f' = A8/(A8+A2)
    s  = (A1+A9)/(g*Ip)
    s' = (A3+A5)/(g*Ip)
(r, r' follow from p^2+r^2+s^2=1, matching Part 1.)

Calibration output is in the SAME {s,f,r,delta_deg,T} schema as Part 1's
qwp_calibration.py for both QWPs, to support an explicit cross-check
report (agreement = confidence in calibration; disagreement = flag for
investigation) -- see cross_check_against_part1().
"""

from __future__ import annotations

from pathlib import Path

import numpy as np

from continuous_dual_arm_reconstruction import THETA_INDICES, _theta, fit_dual_arm_fourier


def _phi(j: int, c1_deg: float, c1_prime_deg: float) -> float:
    """phi_j = theta_j(C1, C1') (Eq. 69) -- the constant phase every
    harmonic accumulates from the arbitrary choice of angular origin."""

    c1 = np.deg2rad(c1_deg)
    c1p = np.deg2rad(c1_prime_deg)
    return float(_theta(j, c1, c1p))


#: Eq. (72)'s theoretical sign of aj at M=I, ideal-ish QWPs (a0/a2/a8/a10
#: all positive; a4 = (3ss'-rr')/2 is NEGATIVE since r*r' ~ 1 dominates).
#: atan2(Bj', Aj') recovers phi_j correctly only when Aj (the target,
#: phase-corrected value) is positive -- if it's negative, atan2 is off by
#: 180 deg (it can't tell "positive Aj rotated by phi" from "negative Aj
#: rotated by phi+180"), so this flips the sign of BOTH components first
#: (which leaves their ratio -- and hence the phase -- unchanged when the
#: sign is +1, and applies exactly the needed +180 deg correction when -1).
_EXPECTED_AJ_SIGN = {2: 1.0, 4: -1.0, 6: 1.0, 8: 1.0, 10: 1.0}


def solve_phase_origins(raw_coeffs: dict) -> dict:
    """Eq. (74)/(75)/(76): from the RAW (primed, phase-uncorrected)
    Fourier fit of a system-absent calibration run, recover C1, C1' from
    the two largest (4th, 6th) harmonics, with 2nd/8th/10th as an
    independent consistency check.

    Each phi_j (and so C1, C1') is reduced to a single scalar (nanmedian)
    even when raw_coeffs holds genuine per-pixel (H, W) maps: like Section
    IV's C1', these are properties of the rotating stages' own mechanical/
    encoder zero, not spatially-varying optical quantities -- and
    correct_coefficients (below) needs a single phi_j per harmonic to
    rotate every pixel's raw coefficient pair by, not a per-pixel angle.
    Found while building the calibration CLI (MMIE_ATOMIC_TARGETS.md
    Category 6): a real measure.py session's full-frame intensities
    crashed the previous direct float() cast."""

    def phi_prime(j: int) -> float:
        sign = _EXPECTED_AJ_SIGN[j]
        phi = -np.arctan2(sign * np.asarray(raw_coeffs[f"b{j}"]), sign * np.asarray(raw_coeffs[f"a{j}"]))
        return float(np.nanmedian(phi))

    phi2, phi4, phi6, phi8, phi10 = (phi_prime(j) for j in (2, 4, 6, 8, 10))
    c1_rad = (phi6 - phi4) / 4.0
    c1_prime_rad = (phi6 + phi4) / 4.0

    return {
        "c1_deg": float(np.degrees(c1_rad)),
        "c1_prime_deg": float(np.degrees(c1_prime_rad)),
        "consistency": {
            "phi4_measured": phi4, "phi4_predicted": (phi10 - phi2) / 2.0,
            "phi6_measured": phi6, "phi6_predicted": (phi10 + phi2) / 2.0,
            "phi8_measured": phi8, "phi8_predicted": phi10 - phi2,
        },
    }


def correct_coefficients(raw_coeffs: dict, c1_deg: float, c1_prime_deg: float) -> dict:
    """Eq. (77): rotate every raw (primed) harmonic pair by its own
    phi_j = theta_j(C1, C1') (Eq. 69) to recover the true (unprimed)
    coefficients. a0 has no phase (constant/DC term, untouched)."""

    corrected = {"a0": raw_coeffs["a0"]}
    for j in THETA_INDICES:
        phi = _phi(j, c1_deg, c1_prime_deg)
        aj_prime, bj_prime = raw_coeffs[f"a{j}"], raw_coeffs[f"b{j}"]
        corrected[f"a{j}"] = aj_prime * np.cos(phi) - bj_prime * np.sin(phi)
        corrected[f"b{j}"] = bj_prime * np.cos(phi) + aj_prime * np.sin(phi)
    return corrected


def measure_defects_and_response(coeffs: dict) -> dict:
    """Eq. (78)/(79): defect parameters and source response from the
    phase-corrected coefficients of a system-absent (M=I) run. r, r'
    follow from p^2+r^2+s^2=1, matching Part 1's compute_calibration."""

    a1, a2, a3, a5, a8, a9, a10 = (
        coeffs["a1"], coeffs["a2"], coeffs["a3"], coeffs["a5"], coeffs["a8"], coeffs["a9"], coeffs["a10"],
    )
    g_ip = 2.0 * (a8 + a10) * (a8 + a2) / a8
    f = a8 / (a8 + a10)
    f_prime = a8 / (a8 + a2)
    s = (a1 + a9) / g_ip
    s_prime = (a3 + a5) / g_ip

    def _defects(s_value, f_value) -> dict:
        p_value = 1.0 - 2.0 * f_value
        radicand = 1.0 - p_value**2 - s_value**2
        r_value = np.sqrt(np.clip(radicand, 0.0, None))
        delta_deg = np.degrees(np.arctan2(r_value, p_value))
        s_clipped = np.clip(s_value, -1.0, 1.0)
        transmission_t = np.tan(0.5 * np.arccos(s_clipped))
        return {"s": s_value, "f": f_value, "p": p_value, "r": r_value, "delta_deg": delta_deg, "T": transmission_t}

    return {
        "g_ip": g_ip,
        "psg_qwp": _defects(s, f),
        "psa_qwp": _defects(s_prime, f_prime),
    }


def run_dual_arm_calibration(c_deg, cp_deg, intensities: np.ndarray) -> dict:
    """Full Sec. V.B sequence from one system-absent 4x4 continuous run:
    fit raw coefficients -> phase origins -> phase-correct -> defects."""

    raw_coeffs = fit_dual_arm_fourier(c_deg, cp_deg, intensities)
    phase = solve_phase_origins(raw_coeffs)
    corrected = correct_coefficients(raw_coeffs, phase["c1_deg"], phase["c1_prime_deg"])
    defects = measure_defects_and_response(corrected)
    return {
        "c1_deg": phase["c1_deg"],
        "c1_prime_deg": phase["c1_prime_deg"],
        "phase_consistency": phase["consistency"],
        "g_ip": defects["g_ip"],
        "psg_qwp": defects["psg_qwp"],
        "psa_qwp": defects["psa_qwp"],
        "raw_b_magnitude_check": {
            f"b{j}": float(np.max(np.abs(np.asarray(raw_coeffs[f"b{j}"])))) for j in THETA_INDICES
        },
    }


def cross_check_against_part1(dual_arm_result: dict, part1_summaries: dict, aggregation: str) -> dict:
    """Compares this module's recovered PSG_QWP/PSA_QWP s,f,r,delta_deg,T
    against Part 1's qwp_calibration.py summaries for the SAME two QWPs.
    part1_summaries: {"PSG_QWP": <summary dict>, "PSA_QWP": <summary dict>}
    (Part 1's Config/calibration_result.json "results"/<target>/"summary").
    Agreement = confidence in both calibrations; disagreement flags
    something to investigate."""

    diffs: dict = {}
    for target, key in (("PSG_QWP", "psg_qwp"), ("PSA_QWP", "psa_qwp")):
        target_diffs = {}
        for field in ("s", "f", "r", "delta_deg", "T"):
            this_value = float(np.asarray(dual_arm_result[key][field]))
            part1_value = float(part1_summaries[target][field][aggregation])
            target_diffs[field] = {"continuous_dual_arm": this_value, "part1_discrete": part1_value, "diff": this_value - part1_value}
        diffs[target] = target_diffs
    return diffs


# ============================================================================
# I/O -- reads a real, sample-absent measure.py 4x4 continuous-mode session
# and drives run_dual_arm_calibration() from it (MMIE_ATOMIC_TARGETS.md
# Category 6 -- previously this module was library-only, exercised only by
# test_continuous_dual_arm.py's synthetic arrays).
# ============================================================================

def load_and_run_dual_arm_calibration(run_dir) -> dict:
    """Reads a sample-absent measure.py 4x4 continuous-mode session (via
    continuous_dual_arm_reconstruction.load_dual_arm_run, the same loader
    the reconstruction CLI uses for a real sample) and runs the full
    Sec. V.B sequence above. Unlike Section IV, no separate phase-reference
    revolution needs to be located -- C1/C1' both come from one 25-
    coefficient fit of the whole session (Eq. 74/75), so
    load_dual_arm_run's (c_deg, cp_deg, intensities) maps directly onto
    run_dual_arm_calibration's own signature."""

    from continuous_dual_arm_reconstruction import load_dual_arm_run

    data = load_dual_arm_run(run_dir)
    return run_dual_arm_calibration(data["c_deg"], data["cp_deg"], data["intensities"])


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


def summarize_qwp_side(qwp_side: dict, roi, aggregation: str) -> dict:
    """Reduces run_dual_arm_calibration()'s per-pixel psg_qwp/psa_qwp dict
    to a flat {field: float} dict via the ROI -- the shape both printing
    and cross_check_against_part1() need."""

    return {key: _reduce_to_scalar(value, roi, aggregation) for key, value in qwp_side.items()}


def _infer_default_roi(result: dict):
    """Whole-frame ROI derived from PSG_QWP's recovered s-map shape, or
    None if the result is already scalar (non-per-pixel) end to end."""

    s_array = np.asarray(result["psg_qwp"]["s"])
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
        description="Hauge (1978) Section V.B built-in calibration + cross-check against Section II"
    )
    parser.add_argument(
        "run_dir", type=Path,
        help="measure.py 4x4 continuous-mode, SAMPLE-ABSENT session directory (Data/<run>/)",
    )
    parser.add_argument(
        "--compare-to", type=Path, default=None, metavar="CALIBRATION_RESULT_JSON",
        help="Section II qwp_calibration.py's Config/calibration_result.json (both QWPs), to cross-check against",
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

    args = _parse_args()
    result = load_and_run_dual_arm_calibration(args.run_dir)
    roi = tuple(args.roi) if args.roi else _infer_default_roi(result)

    c1_deg = _reduce_to_scalar(result["c1_deg"], roi, args.aggregation)
    c1_prime_deg = _reduce_to_scalar(result["c1_prime_deg"], roi, args.aggregation)
    g_ip = _reduce_to_scalar(result["g_ip"], roi, args.aggregation)
    psg_summary = summarize_qwp_side(result["psg_qwp"], roi, args.aggregation)
    psa_summary = summarize_qwp_side(result["psa_qwp"], roi, args.aggregation)
    b_magnitude = {key: _reduce_to_scalar(value, roi, args.aggregation) for key, value in result["raw_b_magnitude_check"].items()}
    max_b_magnitude = max(b_magnitude.values())

    print("Mode: 4x4 (dual-arm continuous, sample absent)")
    print(f"C1: {c1_deg:.5f} deg   C1': {c1_prime_deg:.5f} deg")
    print(f"Source response g*Ip: {g_ip:.6f}")
    print(f"Max |raw b_j| (should be ~0 -- large values indicate misalignment): {max_b_magnitude:.3e}")
    print("\n--- PSG_QWP recovered defect parameters (continuous, Sec. V.B) ---")
    for key in ("s", "f", "r", "delta_deg", "T"):
        print(f"  {key:10s} = {psg_summary[key]:.5f}")
    print("\n--- PSA_QWP recovered defect parameters (continuous, Sec. V.B) ---")
    for key in ("s", "f", "r", "delta_deg", "T"):
        print(f"  {key:10s} = {psa_summary[key]:.5f}")

    report: dict = {
        "mode": "4x4",
        "aggregation": args.aggregation,
        "roi": {"x": roi[0], "y": roi[1], "width": roi[2], "height": roi[3]} if roi else None,
        "c1_deg": c1_deg,
        "c1_prime_deg": c1_prime_deg,
        "source_response_g_ip": g_ip,
        "raw_b_magnitude_check": b_magnitude,
        "psg_qwp": psg_summary,
        "psa_qwp": psa_summary,
    }

    if args.compare_to is not None:
        part1_report = json.loads(args.compare_to.read_text(encoding="utf-8"))
        available = list(part1_report.get("results", {}))
        missing = [target for target in ("PSG_QWP", "PSA_QWP") if target not in part1_report.get("results", {})]
        if missing:
            raise ValueError(f"{args.compare_to} is missing calibration results for {missing} (found: {available}).")
        part1_summaries = {
            "PSG_QWP": part1_report["results"]["PSG_QWP"]["summary"],
            "PSA_QWP": part1_report["results"]["PSA_QWP"]["summary"],
        }
        part1_aggregation = part1_report["aggregation"]
        diffs = cross_check_against_part1(
            {"psg_qwp": psg_summary, "psa_qwp": psa_summary}, part1_summaries, part1_aggregation
        )
        report["cross_check_against_part1"] = diffs
        print(f"\n--- Cross-check against Section II ({args.compare_to}, aggregation={part1_aggregation!r}) ---")
        for target, target_diffs in diffs.items():
            print(f"  {target}:")
            for field, values in target_diffs.items():
                print(
                    f"    {field:10s} continuous={values['continuous_dual_arm']:.5f}  "
                    f"part1_discrete={values['part1_discrete']:.5f}  diff={values['diff']:+.5f}"
                )

    results_dir = Path(args.run_dir) / "Results"
    results_dir.mkdir(parents=True, exist_ok=True)
    out_path = results_dir / "dual_arm_calibration_cross_check.json"
    out_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(f"\nSaved: {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
