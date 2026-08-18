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

import numpy as np

from continuous_single_arm_reconstruction import fit_outer_fourier, fit_revolution_fourier, harmonic_matrix_polarizer


def measure_phase_offset(angles_deg, intensities: np.ndarray) -> float:
    """Eq. (57): tan(4*C1') = -B4'/A4', from the RAW (uncorrected) per-
    revolution Fourier fit at the fixed-side reference, system absent.
    Returns C1' in degrees (principal branch; the true phase is only
    determined mod 90 deg -- Eq. 57's own tan(4*C1') ambiguity -- same
    fast/slow-axis-style branch caveat as Part 1's null search)."""

    fourier = fit_revolution_fourier(angles_deg, np.asarray(intensities, dtype=np.float64))
    a4, b4 = fourier["A4"], fourier["B4"]
    return float(np.degrees(np.arctan2(-b4, a4)) / 4.0)


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
    since neither mode has a real compensator on the outer side."""

    s_estimates = {"E[0,2]": float(np.asarray(e_mat)[0, 2]), "E[1,2]": float(np.asarray(e_mat)[1, 2])}
    f_estimates = {"E[1,4]": float(np.asarray(e_mat)[1, 4]), "1-E[1,0]": float(1.0 - np.asarray(e_mat)[1, 0])}
    expected_b = harmonic_matrix_polarizer()
    max_deviation = float(np.max(np.abs(np.asarray(e_mat) - expected_b)))
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
    response."""

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
