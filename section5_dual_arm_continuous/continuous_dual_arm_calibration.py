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
    independent consistency check."""

    def phi_prime(j: int) -> float:
        sign = _EXPECTED_AJ_SIGN[j]
        return float(-np.arctan2(sign * np.asarray(raw_coeffs[f"b{j}"]), sign * np.asarray(raw_coeffs[f"a{j}"])))

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
