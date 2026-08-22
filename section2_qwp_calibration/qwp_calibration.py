"""qwp_calibration.py -- Hauge (1978) Section II: null-search + three-angle
QWP retardance/diattenuation calibration.

Measures the ACTUAL retardance (delta_C) and diattenuation (T) of both
QWPs, in situ, in their real mounted positions -- with no assumption that
delta=90 deg or that ZERO_OFFSETS_DEG is already correct. Implements JOSA
68(11), 1519 (1978):

  Step 0 (null-search, this file's main addition over the original draft):
    - Null A against fixed P (all compensators removed) -- rotate A to the
      intensity MINIMUM. That motor angle, 90 deg away by definition
      (Section II.B: "The analyzer is inserted and adjusted for null so
      that A = 90 deg"), fixes A's discovered zero-offset.
    - Insert the compensator under test between the still-crossed P/A;
      rotate it to the intensity minimum ("null restored"). That motor
      angle, by definition C = 0 deg, fixes that compensator's discovered
      zero-offset (Section II.B: "The compensator C is inserted and
      adjusted for the null at C = 0 deg").
    - Both QWPs are calibrated back-to-back in one session; the operator
      confirms the physical swap (which QWP is in the beam) via prompt.

  Step 1-4 (already correct in the original draft -- kept unchanged):
    - Eq. (19): three intensity readings R1,R2,R3 at compensator azimuths
      C = 0, 45, 90 deg, with P=A=0 (parallel, using the just-discovered
      zero-offsets), no sample, and the OTHER QWP physically removed.
    - Eq. (21): s = (R1-R3)/(R1+R3),  f = (R1-2*R2+R3)/(R1+R3)
    - Eq. (4):  p = 1-2f,  r = sqrt(1-p^2-s^2)
    - delta_C = atan2(r, p)   (retardance)
    - T = tan(0.5*arccos(s))  (diattenuation)

Dry-run (--dry-run) needs no Kinesis/pythonnet/IDS Peak installed. A small
synthetic optical-bench model (DryRunOpticalBench, below) gives the
automated null search a genuine minimum to converge on -- driven by hidden
"true" zero-offset errors and hidden compensator defects -- rather than
trivially succeeding regardless of the search logic.

Reuses camera_communication.IDSCamera and motor_communication.CageRotatorMotor
unchanged; this file's own dry-run bench is used only for the null-search
scalar intensities, and is layered on top of (not a replacement for) the
normal capture_tiff/acquire_array path used for real hardware.
"""

from __future__ import annotations

import argparse
import json
import math
import sys
import time
from datetime import datetime
from pathlib import Path

# Project layout: this file lives in section2_qwp_calibration/, while the
# thin hardware wrappers it reuses as-is live in ../common/ (shared by
# every section). Add that folder to sys.path rather than duplicating
# ~500 lines of motor/camera code here.
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_PROJECT_ROOT / "common"))

from camera_communication import CameraError, IDSCamera, roi_mean, select_roi  # noqa: E402
from motor_communication import CageRotatorMotor, MotorError  # noqa: E402

# ============================================================================
# USER SETTINGS -- edit before each run
# ============================================================================

# Which QWP(s) to calibrate, back-to-back, in one session.
CALIBRATION_TARGETS = ("PSG_QWP", "PSA_QWP")

# "three_angle" (default): Hauge's own minimal exact solve -- 3 equations,
# 2 unknowns (s,f), zero redundancy, at exactly 0/45/90 deg. Fast, but
# every frame's noise goes straight into the result.
# "least_squares": generalizes the SAME closed-form relations (Eq. 21) to
# LEAST_SQUARES_NUM_ANGLES > 3 evenly-spaced angles, least-squares fit
# instead of an exact 3-point solve -- reduces noise the same way any
# over-determined fit does, at the cost of more captures per QWP.
CALIBRATION_ANGLE_MODE = "three_angle"

# The three compensator azimuths from Hauge Eq. (19)/(20) -- used when
# CALIBRATION_ANGLE_MODE == "three_angle". Do not change unless you also
# change compute_calibration() -- the closed-form algebra is derived
# specifically for 0/45/90.
CALIBRATION_ANGLES_DEG = (0.0, 45.0, 90.0)

# Used when CALIBRATION_ANGLE_MODE == "least_squares": N angles spread
# evenly across one full period (0-180 deg -- R(C) repeats every 180 deg,
# see fit_calibration_least_squares' docstring), fit instead of solved.
LEAST_SQUARES_NUM_ANGLES = 12

# Same serials/offsets convention as measure.py -- keep these two files in
# sync by hand (deliberate duplication, same "each consumer owns its own
# copy" convention already used for the pre-flight rank check).
MOTOR_SERIALS = {
    "PSG_Polarizer": "55542004",
    "PSG_QWP": "55542914",
    "PSA_QWP": "55542224",
    "PSA_Analyzer": "55542504",
}
ZERO_OFFSETS_DEG = {
    "PSG_Polarizer": 121.7,
    "PSG_QWP": 66.4,
    "PSA_QWP": 174.07,
    "PSA_Analyzer": 45.02,
}

# --- camera (match your working measure.py settings) ---
CAMERA_EXPOSURE_US = 390
CAMERA_FRAME_RATE_FPS = 34.92
CAMERA_GAIN = 1.0
CAMERA_PIXEL_FORMAT = "Mono8"
CAMERA_TIMEOUT_MS = 5000
CAMERA_RETRIES = 2

# --- motors ---
HOME_MOTORS = True
HOME_SPEED_DEG_S = 10.0
MOVE_VELOCITY_DEG_S = 10.0
MOVE_ACCELERATION_DEG_S2 = 20.0
MOVE_TIMEOUT_MS = 60_000
POSITION_TOLERANCE_DEG = 0.1
MOTOR_SETTLE_S = 0.3

# --- dark subtraction ---
DARK_SUBTRACT = True  # if True, prompts you to block the beam and captures a dark frame

# --- ROI for the summary numbers (per-pixel maps are always saved in full) ---
ROI = None  # None = auto-select flattest bright region; or (x, y, width, height)
ROI_WINDOW_SIZE = 200
ROI_STRIDE = 100
ROI_MIN_MEAN = 20.0

# "median" or "mean" -- which statistic summarizes the ROI into one number
AGGREGATION = "median"

# --- Step-0 null search ---
NULL_SEARCH_MODE = "automated"  # "automated" (default) or "interactive" (fallback/manual override)
NULL_SEARCH_COARSE_STEP_DEG = 5.0  # coarse bracketing grid, one full rotation
NULL_SEARCH_TOLERANCE_DEG = 0.05  # golden-section refinement target
NULL_SEARCH_SETTLE_S = 0.15  # settle time after each search move, before reading intensity
SAVE_NULL_SEARCH_FRAMES = False  # True saves every search-loop TIFF (many files); False saves only the final confirmation frame per stage

# --- sanity-check thresholds (Part 1 spec) ---
SANITY_S_ABS_MAX = 0.15  # |s| beyond this suggests a bad null, not necessarily a bad QWP
SANITY_F_DEVIATION_MAX = 0.15  # |f - 0.5| beyond this suggests a bad null
SANITY_DELTA_MIN_DEG = 80.0
SANITY_DELTA_MAX_DEG = 100.0

# Engineering addition, not from Hauge: crossed P/A alone sets a hard
# intensity floor (that pair's own extinction ratio, dark counts, stray
# light); a properly-aligned compensator inserted between them can only
# ADD to that floor (extra glass surfaces, its own residual diattenuation),
# never read darker. A compensator null much brighter than the bare P/A
# null is a real signal -- wrong axis, misalignment, dirty optics, or
# stray light -- not just noise. Two-part threshold (ratio AND absolute
# margin) so two nulls already sitting near the camera's dark-count floor
# don't get flagged over noise-level differences that satisfy the ratio
# alone.
SANITY_NULL_INTENSITY_RATIO_MAX = 1.5
SANITY_NULL_INTENSITY_ABS_MARGIN = 5.0


# Anchored to the project root (not the cwd) so `Data/` always lands in
# the same top-level place regardless of which folder you run this from.
OUTPUT_ROOT = _PROJECT_ROOT / "Data" / "QWP_Calibration"
DRY_RUN = False


# ============================================================================
# Axis roles
# ============================================================================

QWP_ROLES = {
    "PSG_QWP": {"other_qwp": "PSA_QWP", "linear_axes": ("PSG_Polarizer", "PSA_Analyzer")},
    "PSA_QWP": {"other_qwp": "PSG_QWP", "linear_axes": ("PSG_Polarizer", "PSA_Analyzer")},
}


def optical_to_motor(axis: str, optical_deg: float, zero_offsets: dict | None = None) -> float:
    offsets = zero_offsets if zero_offsets is not None else ZERO_OFFSETS_DEG
    return (float(optical_deg) + float(offsets[axis])) % 360.0


# ============================================================================
# Calibration math -- Hauge Eqs. (19)-(21) and (4), vectorized over pixels
# (unchanged from the original draft -- already correct; Part 1 only adds
# the null-search that feeds this function real, discovered angles)
# ============================================================================

def _finish_calibration_maps(s, f, g_ip) -> dict:
    """Shared by compute_calibration() and fit_calibration_least_squares():
    both reduce to the same three numbers (s, f, gIp) per pixel; Eq. (4)'s
    p/r/delta/T derivation from there is identical either way."""

    import numpy as np

    # Eq. (4): p = 1-2f ; r follows from p^2+r^2+s^2=1 (always exactly 1
    # in theory -- this identity is what lets r come "for free" from s,f)
    p = 1.0 - 2.0 * f
    radicand = 1.0 - p**2 - s**2
    n_bad = int(np.sum(radicand < 0))
    if n_bad:
        print(
            f"  WARNING: {n_bad} pixel(s) have p^2+s^2 > 1 (noise/misalignment "
            "pushed the measurement outside the physically realizable range); "
            "clipping radicand to 0 there."
        )
    r = np.sqrt(np.clip(radicand, 0.0, None))

    # retardance -- atan2, NOT atan(r/p), so sign of p is handled correctly
    # for delta_C straying either side of 90 deg
    delta_deg = np.degrees(np.arctan2(r, p))

    # diattenuation (transmission-ratio defect), a bonus this method gives
    s_clipped = np.clip(s, -1.0, 1.0)
    T = np.tan(0.5 * np.arccos(s_clipped))

    return {
        "s": s, "f": f, "p": p, "r": r,
        "delta_deg": delta_deg, "T": T,
        "gIp": g_ip,
        "n_unphysical_pixels": n_bad,
    }


def compute_calibration(frames: dict) -> dict:
    """frames: {0.0: array, 45.0: array, 90.0: array}, already dark-
    subtracted float arrays of identical shape. Returns per-pixel maps.

    Hauge's own minimal exact solve: 3 equations (R1,R2,R3), 2 unknowns
    (s,f), zero redundancy -- every frame's noise goes straight into s,f.
    fit_calibration_least_squares() below is the same algebra generalized
    to more angles for lower noise."""

    import numpy as np

    R1 = frames[0.0].astype(np.float64)
    R2 = frames[45.0].astype(np.float64)
    R3 = frames[90.0].astype(np.float64)

    denom = R1 + R3
    denom_safe = np.where(denom == 0, np.nan, denom)

    # Eq. (21)
    s = (R1 - R3) / denom_safe
    f = (R1 - 2.0 * R2 + R3) / denom_safe
    g_ip = denom / 2.0  # Eq. (22)-equivalent source-response check

    return _finish_calibration_maps(s, f, g_ip)


def least_squares_calibration_angles(num_angles: int) -> tuple[float, ...]:
    """N angles spread evenly across one full period. R(C) at P=A=0 (Eq. 8
    collapsed, per DryRunOpticalBench.calibration_reading's derivation)
    is 1 - f/2 + s*cos(2C) + (f/2)*cos(4C) -- period 180 deg (cos(2C)
    needs the full 180 to repeat; cos(4C) alone would only need 90, but
    doesn't set the period since cos(2C) is also present). Sampling
    0-360 would just repeat the same period twice -- not wrong, but not
    additional information either, so this spans only 0-180.

    N=3 is special-cased to Hauge's own 0/45/90 rather than the general
    evenly-spaced rule (which would give 60 deg spacing): at exactly 60
    deg spacing, cos(2*60)=cos(4*60) and cos(2*120)=cos(4*120) alias
    against each other, making the reduced 3-parameter design matrix
    singular -- the same aliasing class measure.py's own pre-flight rank
    check guards against for QWP angles spaced 90 deg apart. N>=4 does
    not have this problem (verified: full rank for every N=4..15)."""

    if num_angles == 3:
        return CALIBRATION_ANGLES_DEG

    import numpy as np

    return tuple(float(a) for a in np.linspace(0.0, 180.0, num_angles, endpoint=False))


def fit_calibration_least_squares(frames: dict) -> dict:
    """frames: {angle_deg: array, ...} for N >= 3 angles (see
    least_squares_calibration_angles), already dark-subtracted float
    arrays of identical shape. Returns the same per-pixel map schema as
    compute_calibration(), plus a "diagnostics" entry.

    Core recovery generalizes Hauge Eq. (19)-(22) beyond his own minimal
    3-point case: least-squares fits the REDUCED 3-term model
    R(C) = A0 + A2*cos(2C) + A4*cos(4C) (B2=B4=0 assumed, exactly Hauge's
    own reference-config premise, not fit) against every logged angle at
    once (a single lstsq call handles every pixel, since the design matrix
    depends only on the angles, not the calibration). At N=3 this is not
    an approximation -- lstsq on an exactly-determined 3x3 system returns
    the same unique solution Eq. (21) derives by hand.

    Recovery, re-derived from Eq. (19)'s own R1=(1+s)*gIp, R2=(1-f)*gIp,
    R3=(1-s)*gIp: gIp = A0+A4, s = A2/(A0+A4), f = 2*A4/(A0+A4). Averaging
    over N > 3 points reduces the effect of per-frame noise on s,f the
    same way any over-determined least-squares fit does.

    Bonus diagnostic (only when N >= 5, since fitting all of A0,A2,B2,A4,B4
    needs at least 5 equations -- fewer would be an underdetermined system,
    which lstsq would silently "solve" via its minimum-norm solution
    instead of raising): a SEPARATE fit of the general 5-term model
    (B2, B4 no longer assumed zero) reports B2, B4 as an actual, independent
    check on whether P/A really are at their assumed 0/90 reference --
    large values indicate misalignment, not the compensator itself."""

    import numpy as np

    angles_deg = sorted(frames.keys())
    if len(angles_deg) < 3:
        raise ValueError(f"Need at least 3 calibration angles for a least-squares fit; got {len(angles_deg)}.")
    theta = np.deg2rad(np.array(angles_deg, dtype=np.float64))
    stacked = np.stack([frames[angle].astype(np.float64) for angle in angles_deg], axis=0)  # (N, H, W)
    pixel_shape = stacked.shape[1:]
    flat = stacked.reshape(stacked.shape[0], -1)

    design_reduced = np.stack([np.ones_like(theta), np.cos(2 * theta), np.cos(4 * theta)], axis=1)  # (N, 3)
    coeffs_reduced, *_ = np.linalg.lstsq(design_reduced, flat, rcond=None)  # (3, n_pixels)
    a0, a2, a4 = coeffs_reduced.reshape((3,) + pixel_shape)

    g_ip = a0 + a4
    g_ip_safe = np.where(g_ip == 0, np.nan, g_ip)
    s = a2 / g_ip_safe
    f = 2.0 * a4 / g_ip_safe

    maps = _finish_calibration_maps(s, f, g_ip)
    diagnostics = {"num_angles": len(angles_deg), "B2": None, "B4": None}
    if len(angles_deg) >= 5:
        design_full = np.stack(
            [np.ones_like(theta), np.cos(2 * theta), np.sin(2 * theta), np.cos(4 * theta), np.sin(4 * theta)], axis=1
        )  # (N, 5)
        coeffs_full, *_ = np.linalg.lstsq(design_full, flat, rcond=None)  # (5, n_pixels)
        _, _, b2, _, b4 = coeffs_full.reshape((5,) + pixel_shape)
        diagnostics["B2"] = b2
        diagnostics["B4"] = b4
    maps["diagnostics"] = diagnostics
    return maps


def summarize_roi(maps: dict, roi: tuple, aggregation: str) -> dict:
    import numpy as np

    x, y, w, h = roi
    stat = np.nanmedian if aggregation == "median" else np.nanmean
    summary = {}
    for key in ("s", "f", "p", "r", "delta_deg", "T", "gIp"):
        region = maps[key][y : y + h, x : x + w]
        summary[key] = {
            aggregation: float(stat(region)),
            "std": float(np.nanstd(region)),
        }
    return summary


def sanity_check_warnings(summary: dict, aggregation: str) -> list[str]:
    """Part 1 spec: flag a bad null (not necessarily a bad QWP)."""

    warnings = []
    s_val = summary["s"][aggregation]
    f_val = summary["f"][aggregation]
    delta_val = summary["delta_deg"][aggregation]
    if abs(s_val) > SANITY_S_ABS_MAX:
        warnings.append(f"|s|={abs(s_val):.4f} exceeds {SANITY_S_ABS_MAX} -- suspect a bad null, not necessarily a bad QWP.")
    if abs(f_val - 0.5) > SANITY_F_DEVIATION_MAX:
        warnings.append(f"|f-0.5|={abs(f_val - 0.5):.4f} exceeds {SANITY_F_DEVIATION_MAX} -- suspect a bad null, not necessarily a bad QWP.")
    if not (SANITY_DELTA_MIN_DEG <= delta_val <= SANITY_DELTA_MAX_DEG):
        warnings.append(f"delta_deg={delta_val:.4f} is outside [{SANITY_DELTA_MIN_DEG}, {SANITY_DELTA_MAX_DEG}].")
    return warnings


def null_intensity_mismatch_warning(pa_null_intensity: float, compensator_null_intensity: float, target: str) -> str | None:
    """Engineering addition, not a Hauge equation: crossed P/A alone sets
    a hard intensity floor; a properly-aligned compensator between them
    can only add to that floor (extra surfaces, its own residual
    diattenuation), never read darker. A compensator null much brighter
    than the bare P/A null flags a real problem -- wrong axis, a partial
    misalignment, dirty optics, or stray light -- the same category of
    thing SANITY_S_ABS_MAX/SANITY_DELTA_*_DEG already catch for a
    different failure mode. Two-part threshold (ratio AND absolute
    margin, both must trip) so two readings already near the camera's
    dark-count floor don't get flagged over noise alone."""

    excess = compensator_null_intensity - pa_null_intensity
    ratio = compensator_null_intensity / pa_null_intensity if pa_null_intensity > 0 else math.inf
    if ratio > SANITY_NULL_INTENSITY_RATIO_MAX and excess > SANITY_NULL_INTENSITY_ABS_MARGIN:
        return (
            f"[{target}] compensator null intensity ({compensator_null_intensity:.3f}) is "
            f"{ratio:.2f}x the P-vs-A null intensity ({pa_null_intensity:.3f}), exceeding "
            f"{SANITY_NULL_INTENSITY_RATIO_MAX}x by more than {SANITY_NULL_INTENSITY_ABS_MARGIN} "
            "counts -- suspect a bad compensator null (wrong axis, misalignment, dirty optics, "
            "or stray light), not just noise."
        )
    return None


# ============================================================================
# Dry-run synthetic optical bench -- gives the automated null search a real
# minimum to converge on. Hidden ground-truth values are intentionally not
# equal to the assumed ZERO_OFFSETS_DEG / an ideal QWP, so a search that
# doesn't actually work would fail a round-trip check against them.
# ============================================================================

class DryRunOpticalBench:
    """Synthetic Malus-law model driven by the *actual* simulated motor
    positions (motor_communication.CageRotatorMotor's dry-run tracks a real
    simulated angle per move). Used only when dry_run=True, in place of
    camera_communication.IDSCamera's generic gradient simulation, so Step 0's
    search logic has something physically real to find -- not a scripted
    trivial success.
    """

    # Hidden ground-truth zero-offset ERRORS (deg): how far each axis's
    # *true* physical zero differs from the assumed ZERO_OFFSETS_DEG. A
    # working null search should discover
    # (ZERO_OFFSETS_DEG[axis] + this value) to within NULL_SEARCH_TOLERANCE_DEG.
    HIDDEN_OFFSET_ERROR_DEG = {"PSA_Analyzer": 1.83, "PSG_QWP": -0.62, "PSA_QWP": 0.94}

    # Hidden ground-truth compensator defects: each QWP is a slightly
    # imperfect retarder (not exactly s=0, f=0.5), so the 3-angle
    # measurement has something non-trivial to recover and cross-check.
    HIDDEN_DEFECTS = {
        "PSG_QWP": {"s": 0.015, "f": 0.485},
        "PSA_QWP": {"s": -0.020, "f": 0.492},
    }

    NOISE_FLOOR = 8.0
    CONTRAST = 180.0

    def __init__(self, motors: dict) -> None:
        self.motors = motors  # {axis: CageRotatorMotor}

    def _true_optical_deg(self, axis: str) -> float:
        motor_deg = self.motors[axis].read_pos()
        true_zero_offset = ZERO_OFFSETS_DEG[axis] + self.HIDDEN_OFFSET_ERROR_DEG.get(axis, 0.0)
        return (motor_deg - true_zero_offset) % 360.0

    def crossed_polarizer_intensity(self) -> float:
        """P is the fixed reference (true optical 0 by definition); A is at
        its current TRUE optical angle. Ideal-polarizer Malus law:
        2*I_A = 1 + cos(2*A_true) -- minimum at A_true = 90 deg."""

        a_true = self._true_optical_deg("PSA_Analyzer")
        return self.NOISE_FLOOR + self.CONTRAST * 0.5 * (1.0 + math.cos(math.radians(2.0 * a_true)))

    def compensator_null_intensity(self, target: str) -> float:
        """`target` inserted between still-crossed P/A (A held at its just-
        discovered true-90 position); `target` at its current TRUE optical
        angle. Derived from Hauge Eq. (8)/(11) with P=0, A=90, no C':
        2*I_A = f*(1 - cos(4*theta_true)) -- minimum at theta_true = 0 deg
        (mod 90 deg)."""

        theta_true = self._true_optical_deg(target)
        f_true = self.HIDDEN_DEFECTS[target]["f"]
        return self.NOISE_FLOOR + self.CONTRAST * f_true * (1.0 - math.cos(math.radians(4.0 * theta_true)))

    def calibration_reading(self, target: str) -> float:
        """R(C) with P=A=0 (parallel), `target` at its CURRENT true optical
        angle (read from the actual simulated motor position, exactly like
        compensator_null_intensity -- the caller must already have moved
        the motor to the desired optical angle relative to the discovered
        zero-offset, same as it would on real hardware). Works for ANY
        angle C, not just 0/45/90 -- used for both CALIBRATION_ANGLE_MODE
        settings. Derived from Hauge Eq. (8) collapsed with A=0 (ideal
        linear analyzer row {1,1,0,0}): 2*I_A = S0+S1 = 1+s*cos2C +
        f*cos4C+s*cos2C+(1-f). Reproduces Hauge Eqs. (19)-(20)'s
        R1=(1+s),R2=(1-f),R3=(1-s) exactly at C=0/45/90 (up to the
        arbitrary gIp scale) when the null search landed on the same
        fast-axis branch the hidden defects are defined against; a
        90-deg-shifted branch (a real, known ambiguity of this null test --
        see Hauge Sec. II.B's "ability to distinguish fast/slow axis"
        caveat) instead reproduces the physically equivalent s-sign-flipped
        reading, not a bug."""

        defects = self.HIDDEN_DEFECTS[target]
        s_true, f_true = defects["s"], defects["f"]
        theta_true = self._true_optical_deg(target)
        c = math.radians(theta_true)
        s0 = 1.0 + s_true * math.cos(2.0 * c)
        s1 = f_true * math.cos(4.0 * c) + s_true * math.cos(2.0 * c) + (1.0 - f_true)
        intensity = 0.5 * (s0 + s1)
        return self.NOISE_FLOOR + self.CONTRAST * intensity


def _dry_run_uniform_frame(intensity: float, height: int = 480, width: int = 640):
    """A synthetic Mono8 frame at the bench's modeled intensity, uniform
    apart from a deterministic ordered dither. Mono8 only has 256 discrete
    levels, so a truly flat frame would truncate every ROI-mean reading to
    a whole integer -- destroying the sub-degree precision the golden-
    section search needs, the same way it would on real hardware without
    the spatial averaging a real ROI provides over many (dithered, noisy)
    real pixels. The dither pattern below reproduces that: it recovers the
    fractional part of `intensity` in the ROI mean, exactly as real-camera
    ROI-averaging recovers sub-count precision from real pixel variation."""

    import numpy as np

    value = float(np.clip(intensity, 0.0, 255.0))
    base = np.floor(value)
    frac = value - base
    y, x = np.indices((height, width))
    threshold = ((x * 37 + y * 17) % 1000) / 1000.0
    frame = np.where(threshold < frac, base + 1.0, base)
    return np.clip(frame, 0.0, 255.0).astype(np.uint8)


# ============================================================================
# Capture + ROI-intensity helpers
# ============================================================================

def capture_frame(camera: IDSCamera, path: Path, retries: int):
    """Acquire + save a TIFF + return the array, with simple retries.
    Mirrors camera_communication.IDSCamera.capture_tiff's retry pattern
    but also hands back the raw array for the per-pixel calibration math."""

    from PIL import Image

    last_error = None
    for attempt in range(1, retries + 2):
        try:
            image = camera.acquire_array()
            path.parent.mkdir(parents=True, exist_ok=True)
            Image.fromarray(image).save(path, format="TIFF", compression="raw")
            sat = int((image == 255).sum())
            print(f"  Saved {path.name}: mean={image.mean():.3f}, max={int(image.max())}"
                  + (f", {sat} saturated px" if sat else ""))
            return image
        except Exception as exc:
            last_error = exc
            print(f"  Capture attempt {attempt}/{retries + 1} failed: {exc}")
            if attempt <= retries:
                time.sleep(1.0)
    raise CameraError(f"Capture failed: {last_error}") from last_error


def _capture_or_simulate(camera: IDSCamera, path: Path, retries: int, dry_run: bool, dry_run_intensity_fn):
    """Acquire+save a real frame via capture_frame, or synthesize+save a
    dry-run one via _dry_run_uniform_frame -- unifies real/dry-run capture
    into one call so EVERY capture site (bright/dark reference,
    calibration-angle images) writes a real TIFF to disk either way.
    Needed so a separate reconstruction script
    (qwp_calibration_reconstruction.py) can read a dry-run session's
    images back from a real folder the same way it already would a real
    session's -- before this, dry-run frames only ever existed in memory,
    the one hold-out from this project's own convention that dry-run
    always writes files (matches common/camera_communication.py's own
    dry-run branch)."""

    if dry_run:
        from PIL import Image

        array = _dry_run_uniform_frame(dry_run_intensity_fn())
        path.parent.mkdir(parents=True, exist_ok=True)
        Image.fromarray(array).save(path, format="TIFF", compression="raw")
        return array
    return capture_frame(camera, path, retries)


def make_intensity_reader(
    camera: IDSCamera,
    roi: tuple | None,
    images_dir: Path,
    tag: str,
    dry_run: bool,
    dry_run_model_fn,
    retries: int,
    save_every_frame: bool,
):
    """Returns a zero-arg function: capture one frame (real or synthetic),
    return its ROI-mean (or whole-frame mean if roi is None). `tag` names
    the search stage for any saved TIFFs. Used by both the automated grid/
    golden-section search and the interactive nudge loop -- every call is a
    real capture (or its dry-run equivalent), never a cached value."""

    import numpy as np

    counter = {"n": 0}

    def _read() -> float:
        counter["n"] += 1
        path = images_dir / f"{tag}_{counter['n']:04d}.tiff"
        if dry_run:
            intensity = dry_run_model_fn()
            frame = _dry_run_uniform_frame(intensity)
            if save_every_frame:
                from PIL import Image

                path.parent.mkdir(parents=True, exist_ok=True)
                Image.fromarray(frame).save(path, format="TIFF", compression="raw")
        else:
            if save_every_frame:
                frame = capture_frame(camera, path, retries)
            else:
                frame = camera.acquire_array()
        return roi_mean(frame, roi) if roi is not None else float(np.mean(frame))

    return _read


# ============================================================================
# Null search: automated (coarse grid + golden-section) and interactive
# ============================================================================

def _golden_section_minimize(f, lo: float, hi: float, tol_deg: float) -> float:
    """Minimize a unimodal scalar function f over [lo, hi] to within
    tol_deg. Standard golden-section search (no derivatives needed --
    matches Part 1's request for "bisection/golden-section refinement")."""

    invphi = (math.sqrt(5.0) - 1.0) / 2.0  # ~0.618
    a, b = lo, hi
    c = b - invphi * (b - a)
    d = a + invphi * (b - a)
    fc, fd = f(c), f(d)
    while abs(b - a) > tol_deg:
        if fc < fd:
            b, d, fd = d, c, fc
            c = b - invphi * (b - a)
            fc = f(c)
        else:
            a, c, fc = c, d, fd
            d = a + invphi * (b - a)
            fd = f(d)
    return (a + b) / 2.0


def search_null_automated(
    move_to_fn,
    intensity_fn,
    coarse_step_deg: float = NULL_SEARCH_COARSE_STEP_DEG,
    tolerance_deg: float = NULL_SEARCH_TOLERANCE_DEG,
    settle_s: float = NULL_SEARCH_SETTLE_S,
) -> tuple[float, float]:
    """Coarse grid (every coarse_step_deg across a full rotation) to bracket
    the minimum, then golden-section refinement to tolerance_deg. Returns
    (discovered motor angle in deg, raw/un-offset; the achieved ROI-mean
    intensity there) -- the intensity is returned (not just printed) so
    callers can sanity-check it against another null's own achieved
    intensity (see SANITY_NULL_INTENSITY_RATIO_MAX). `move_to_fn(angle_deg)`
    moves the axis to a raw motor angle; `intensity_fn()` captures and
    returns the current ROI-mean intensity."""

    def sample(angle_deg: float) -> float:
        move_to_fn(angle_deg % 360.0)
        if settle_s:
            time.sleep(settle_s)
        return intensity_fn()

    print(f"  Coarse grid search: every {coarse_step_deg:g} deg across 360 deg ...")
    n_steps = int(round(360.0 / coarse_step_deg))
    grid_angles = [i * coarse_step_deg for i in range(n_steps)]
    grid_values = [sample(angle) for angle in grid_angles]
    best_index = min(range(len(grid_values)), key=lambda i: grid_values[i])
    best_angle = grid_angles[best_index]
    print(f"  Coarse minimum near {best_angle:.2f} deg (intensity {grid_values[best_index]:.3f}).")

    print(f"  Golden-section refinement to {tolerance_deg:g} deg tolerance ...")

    def sample_local(offset_deg: float) -> float:
        # Search in a local, unwrapped coordinate centered on best_angle so
        # the bracket never has to cross the awkward 0/360 wraparound.
        return sample(best_angle + offset_deg)

    refined_offset = _golden_section_minimize(sample_local, -coarse_step_deg, coarse_step_deg, tolerance_deg)
    final_angle = (best_angle + refined_offset) % 360.0
    final_intensity = sample(final_angle)
    print(f"  Null found at {final_angle:.4f} deg (intensity {final_intensity:.3f}).")
    return final_angle, final_intensity


def search_null_interactive(move_to_fn, read_angle_fn, intensity_fn) -> tuple[float, float]:
    """Interactive fallback/manual-override: loop of (print current angle +
    ROI intensity, prompt operator for a relative nudge in degrees or
    'done', move, capture, repeat). Returns (angle, intensity) -- the
    intensity is whatever was already read this same iteration (the one
    just printed to the operator), not a fresh extra capture."""

    print("  Interactive null search. Enter a relative nudge in degrees (e.g. 2.5 or -0.3), or 'done'.")
    while True:
        angle = read_angle_fn()
        intensity = intensity_fn()
        print(f"  angle={angle:.4f} deg, ROI intensity={intensity:.3f}")
        answer = input("  Nudge (deg) or 'done': ").strip().lower()
        if answer in ("done", "d", ""):
            return angle, intensity
        try:
            nudge = float(answer)
        except ValueError:
            print("  Not a number and not 'done' -- try again.")
            continue
        move_to_fn((angle + nudge) % 360.0)


def run_one_null_search(
    label: str,
    motor: CageRotatorMotor,
    intensity_fn,
    mode: str,
) -> tuple[float, float]:
    """Dispatches to automated or interactive search, returns (discovered
    raw motor angle of the minimum, the achieved ROI-mean intensity
    there)."""

    print(f"[{label}] Searching for intensity null ({mode}) ...")

    def move_to(angle_deg: float) -> None:
        motor.move_cage_rotator_to(angle_deg % 360.0, timeout_ms=MOVE_TIMEOUT_MS, tolerance_deg=POSITION_TOLERANCE_DEG)

    if mode == "automated":
        return search_null_automated(move_to, intensity_fn)
    if mode == "interactive":
        return search_null_interactive(move_to, motor.read_pos, intensity_fn)
    raise ValueError(f"NULL_SEARCH_MODE must be 'automated' or 'interactive', got {mode!r}.")


# ============================================================================
# Acquisition / orchestration
# ============================================================================

def make_run_directory(label: str) -> Path:
    date_text = datetime.now().strftime("%Y-%m-%d")
    OUTPUT_ROOT.mkdir(parents=True, exist_ok=True)
    for index in range(1, 1000):
        run_dir = OUTPUT_ROOT / f"{date_text}_{label}_{index:02d}"
        try:
            run_dir.mkdir()
            (run_dir / "Images").mkdir()
            return run_dir
        except FileExistsError:
            continue
    raise RuntimeError("More than 999 same-day calibration runs already exist.")


def ask_yes_no(prompt: str, default: bool = False) -> bool:
    suffix = " [Y/n]: " if default else " [y/N]: "
    answer = input(prompt + suffix).strip().lower()
    return default if not answer else answer in {"y", "yes"}


def ask_positive_float(prompt: str, default: float) -> float:
    """Prompt for a number > 0; blank input keeps `default`. Re-prompts on
    anything that doesn't parse as a positive float, so a mistyped value
    never silently falls back to a default the operator didn't ask for."""

    while True:
        answer = input(f"{prompt} [default {default}]: ").strip()
        if not answer:
            return default
        try:
            value = float(answer)
        except ValueError:
            print(f"  Not a number: {answer!r}. Try again.")
            continue
        if value <= 0:
            print("  Enter a number greater than zero.")
            continue
        return value


def ask_text(prompt: str, default: str) -> str:
    """Prompt for a string; blank input keeps `default`."""

    answer = input(f"{prompt} [default {default}]: ").strip()
    return answer if answer else default


def _measure_calibration_angles(
    target: str,
    calibration_angles: tuple[float, ...],
    run_dir: Path,
    motors: dict,
    camera: IDSCamera,
    dry_run: bool,
    bench: "DryRunOpticalBench | None",
    effective_zero_offsets: dict,
) -> None:
    """Step 1-4's IMAGES only (the solve itself now lives in
    run_reconstruction, below): capture and save R(C) at each angle in
    calibration_angles, with P=A=0 (parallel, using the just-discovered
    offsets). Either the 3 fixed Hauge angles (CALIBRATION_ANGLE_MODE ==
    "three_angle") or N evenly-spaced ones (== "least_squares", see
    least_squares_calibration_angles). Saves raw (not dark-subtracted --
    that now happens when run_reconstruction reloads these images, mirroring
    how Section III/IV/V's own reconstruction scripts load their own dark
    reference separately rather than baking subtraction into the saved
    frame) images to Images/<target>/C_<angle>.tiff via
    _capture_or_simulate, real hardware or dry-run alike."""

    for angle in calibration_angles:
        motor_deg = optical_to_motor(target, angle, effective_zero_offsets)
        print(f"[{target}] optical {angle:g} deg -> motor {motor_deg:.4f} deg")
        reported = motors[target].move_cage_rotator_to(
            motor_deg, timeout_ms=MOVE_TIMEOUT_MS, tolerance_deg=POSITION_TOLERANCE_DEG
        )
        if not dry_run:
            time.sleep(MOTOR_SETTLE_S)
            print(f"  Encoder readback: {reported:.4f} deg")
        path = run_dir / "Images" / target / f"C_{angle:g}.tiff"
        _capture_or_simulate(
            camera, path, CAMERA_RETRIES, dry_run,
            (lambda t=target: bench.calibration_reading(t)) if dry_run else None,
        )


def run_acquisition(
    targets: tuple[str, ...],
    null_mode: str,
    dry_run: bool,
    no_prompt: bool,
    angle_mode: str = CALIBRATION_ANGLE_MODE,
    num_least_squares_angles: int = LEAST_SQUARES_NUM_ANGLES,
) -> "Path | None":
    """Hauge Sec. II's acquisition half only: Step 0 (null A vs fixed P,
    once, shared) + per-target (physical-swap prompt, compensator null
    search, dark capture, calibration-angle image capture). No math here
    at all -- run_reconstruction, below, is what turns this into
    s/f/r/delta_deg/T. Saves every image to disk, including in --dry-run
    (via _capture_or_simulate -- previously dry-run frames only existed in
    memory, the one hold-out from this project's own always-writes-files
    dry-run convention), plus a new Config/experiment_config.json
    recording everything run_reconstruction needs that isn't recoverable
    from the images alone. Returns the run directory, or None if the
    operator declined the "both compensators removed" confirmation (no
    run directory is left half-populated in that case -- nothing has been
    opened yet).

    qwp_calibration_capture.py is this function's own CLI entry point.
    run_calibration(), below, calls this then run_reconstruction() in
    sequence for the original one-shot combined flow."""

    for target in targets:
        if target not in QWP_ROLES:
            raise ValueError(f"CALIBRATION_TARGETS entries must be one of {tuple(QWP_ROLES)}, got {target!r}.")
    if angle_mode not in ("three_angle", "least_squares"):
        raise ValueError(f"angle_mode must be 'three_angle' or 'least_squares', got {angle_mode!r}.")

    if angle_mode == "three_angle":
        active_calibration_angles = CALIBRATION_ANGLES_DEG
    else:
        active_calibration_angles = least_squares_calibration_angles(num_least_squares_angles)
        print(f"Least-squares calibration: {num_least_squares_angles} angles -- {active_calibration_angles}")

    active_axes = ("PSG_Polarizer", "PSA_Analyzer", "PSG_QWP", "PSA_QWP")
    for axis in active_axes:
        if axis not in MOTOR_SERIALS or axis not in ZERO_OFFSETS_DEG:
            raise ValueError(f"Missing MOTOR_SERIALS/ZERO_OFFSETS_DEG entry for {axis!r}.")

    run_label = "and".join(targets)
    run_dir = make_run_directory(run_label)
    print(f"Calibrating {', '.join(targets)}. Output: {run_dir}")

    if not dry_run and not no_prompt:
        print("\n*** Both PSG_QWP and PSA_QWP must be physically removed from the beam path now (arms collinear). ***")
        if not ask_yes_no("Both QWPs are physically removed/out of the beam?"):
            print("Aborting: null search requires both compensators out of the beam.")
            return None

    # Velocity/acceleration are needed to move anything at all, so they're
    # asked up front, before P/A even connect. Camera settings (exposure,
    # frame rate, gain, pixel format) are asked later, once P and A are
    # already parked at their approximate parallel/bright reference
    # position -- so if you want to sanity-check exposure against a live
    # image (e.g. in IDS Cockpit) before committing a value, the beam is
    # already in the right state to do that. The values below (module
    # defaults) are used verbatim, unmodified by the code, only as the
    # prompts' starting point (and as the --no-prompt fallback).
    if no_prompt:
        move_velocity_deg_s = MOVE_VELOCITY_DEG_S
        move_acceleration_deg_s2 = MOVE_ACCELERATION_DEG_S2
    else:
        move_velocity_deg_s = ask_positive_float("Motor rotation velocity (deg/s)", MOVE_VELOCITY_DEG_S)
        move_acceleration_deg_s2 = ask_positive_float(
            "Motor rotation acceleration (deg/s^2)", MOVE_ACCELERATION_DEG_S2
        )

    devices: dict[str, CageRotatorMotor] = {}
    camera: "IDSCamera | None" = None

    def _connect_and_home(axis: str) -> None:
        print(f"[{axis}] Connecting motor {MOTOR_SERIALS[axis]} ...")
        motor = CageRotatorMotor(MOTOR_SERIALS[axis], dry_run)
        motor.connect()
        motor.set_velocity(move_velocity_deg_s, move_acceleration_deg_s2)
        devices[axis] = motor
        if HOME_MOTORS:
            print(f"[{axis}] Homing ...")
            motor.home_with_speed(HOME_SPEED_DEG_S, MOVE_TIMEOUT_MS)

    try:
        # Only P and A are needed for Step 0 (fixed-P vs A null search).
        # Each compensator's motor is connected+homed just-in-time, per
        # target below, right after its physical-insertion confirmation --
        # so a QWP stage that isn't plugged in/powered yet doesn't block
        # startup or the P/A null search that doesn't need it.
        for axis in ("PSG_Polarizer", "PSA_Analyzer"):
            _connect_and_home(axis)

        bench = DryRunOpticalBench(devices) if dry_run else None
        effective_zero_offsets = dict(ZERO_OFFSETS_DEG)
        discovered: dict[str, float] = {}

        # P is the fixed reference axis by definition (Hauge: "The polarizer
        # P is fixed by definition at P = 0 deg") -- its own offset is never
        # searched, only A's and each compensator's, relative to it.
        p_motor_deg = optical_to_motor("PSG_Polarizer", 0.0, effective_zero_offsets)
        devices["PSG_Polarizer"].move_cage_rotator_to(
            p_motor_deg, timeout_ms=MOVE_TIMEOUT_MS, tolerance_deg=POSITION_TOLERANCE_DEG
        )
        time.sleep(MOTOR_SETTLE_S)

        images_dir = run_dir / "Images" / "NullSearch"

        # --- Step 0a: null A vs fixed P (once; shared by both targets) ---
        # P/A are now at their ASSUMED (not yet discovered) parallel/bright
        # position -- exact optical-zero alignment isn't needed here, only
        # "close enough to near-maximum throughput" to judge exposure
        # against, since that's what the null search below re-discovers.
        approx_bright_deg = optical_to_motor("PSA_Analyzer", 0.0, effective_zero_offsets)
        devices["PSA_Analyzer"].move_cage_rotator_to(
            approx_bright_deg, timeout_ms=MOVE_TIMEOUT_MS, tolerance_deg=POSITION_TOLERANCE_DEG
        )
        time.sleep(MOTOR_SETTLE_S)

        if no_prompt:
            exposure_us = CAMERA_EXPOSURE_US
            frame_rate_fps = CAMERA_FRAME_RATE_FPS
            gain = CAMERA_GAIN
            pixel_format = CAMERA_PIXEL_FORMAT
        else:
            if not dry_run:
                print(
                    "\n*** PSG_Polarizer and PSA_Analyzer are now parked at their approximate "
                    "parallel (brightest) reference position. If you want to check the live "
                    "image for saturation in the camera's own software (e.g. IDS Cockpit) before "
                    "choosing an exposure, do that now and close it before continuing -- only one "
                    "program can hold the camera at a time. ***"
                )
            exposure_ms = ask_positive_float("Camera exposure time (ms)", CAMERA_EXPOSURE_US / 1000.0)
            exposure_us = exposure_ms * 1000.0
            frame_rate_fps = ask_positive_float("Camera frame rate (fps)", CAMERA_FRAME_RATE_FPS)
            gain = ask_positive_float("Camera gain", CAMERA_GAIN)
            pixel_format = ask_text("Camera pixel format", CAMERA_PIXEL_FORMAT)

        camera = IDSCamera(
            dry_run, exposure_us, frame_rate_fps, gain,
            pixel_format, CAMERA_TIMEOUT_MS, CAMERA_RETRIES,
        )
        camera.open()

        bright_frame = _capture_or_simulate(
            camera, run_dir / "Images" / "bright_reference.tiff", CAMERA_RETRIES, dry_run,
            (bench.crossed_polarizer_intensity if dry_run else None),
        )
        roi = ROI
        if roi is None:
            reference = bright_frame.astype("uint8") if bright_frame.max() <= 255 else bright_frame
            roi = select_roi(reference, ROI_WINDOW_SIZE, ROI_STRIDE, ROI_MIN_MEAN)
        print(f"ROI: x={roi[0]}, y={roi[1]}, w={roi[2]}, h={roi[3]}")

        a_reader = make_intensity_reader(
            camera, roi, images_dir, "A_vs_P", dry_run,
            (bench.crossed_polarizer_intensity if dry_run else None), CAMERA_RETRIES, SAVE_NULL_SEARCH_FRAMES,
        )
        a_null_motor_deg, a_null_intensity = run_one_null_search("PSA_Analyzer vs P", devices["PSA_Analyzer"], a_reader, null_mode)
        discovered_offset_a = (a_null_motor_deg - 90.0) % 360.0
        effective_zero_offsets["PSA_Analyzer"] = discovered_offset_a
        print(
            f"[PSA_Analyzer] discovered zero-offset {discovered_offset_a:.4f} deg "
            f"(was {ZERO_OFFSETS_DEG['PSA_Analyzer']:.4f} deg, "
            f"diff {discovered_offset_a - ZERO_OFFSETS_DEG['PSA_Analyzer']:+.4f} deg)."
        )
        discovered["PSA_Analyzer"] = discovered_offset_a

        captured_targets: list[str] = []
        null_search_report: dict = {}

        for target in targets:
            role = QWP_ROLES[target]
            other_qwp = role["other_qwp"]
            print(f"\n=== Calibrating {target} ===")
            if not dry_run and not no_prompt:
                print(f"\n*** Physically insert/connect {target} into the beam; confirm {other_qwp} is still removed. ***")
                if not ask_yes_no(f"{target} inserted/connected and {other_qwp} still removed/out of the beam?"):
                    print(f"Aborting {target}: cannot calibrate with {other_qwp} still in the beam or {target} missing.")
                    continue
            if target not in devices:
                _connect_and_home(target)

            # Keep A parked at the just-discovered crossed position while
            # searching for this compensator's null (Hauge: "insert the
            # compensator under test between the still-crossed P/A").
            devices["PSA_Analyzer"].move_cage_rotator_to(
                a_null_motor_deg, timeout_ms=MOVE_TIMEOUT_MS, tolerance_deg=POSITION_TOLERANCE_DEG
            )
            time.sleep(MOTOR_SETTLE_S)

            c_reader = make_intensity_reader(
                camera, roi, images_dir, f"{target}_null", dry_run,
                ((lambda t=target: bench.compensator_null_intensity(t)) if dry_run else None),
                CAMERA_RETRIES, SAVE_NULL_SEARCH_FRAMES,
            )
            c_null_motor_deg, c_null_intensity = run_one_null_search(f"{target} vs crossed P/A", devices[target], c_reader, null_mode)
            discovered_offset_c = c_null_motor_deg % 360.0
            effective_zero_offsets[target] = discovered_offset_c
            print(
                f"[{target}] discovered zero-offset {discovered_offset_c:.4f} deg "
                f"(was {ZERO_OFFSETS_DEG[target]:.4f} deg, "
                f"diff {discovered_offset_c - ZERO_OFFSETS_DEG[target]:+.4f} deg)."
            )
            discovered[target] = discovered_offset_c
            null_search_report[target] = {"pa_null_intensity": a_null_intensity, "compensator_null_intensity": c_null_intensity}

            # --- Step 1-4: three-angle measurement, P=A=0 parallel ---
            a_parallel_deg = optical_to_motor("PSA_Analyzer", 0.0, effective_zero_offsets)
            devices["PSA_Analyzer"].move_cage_rotator_to(
                a_parallel_deg, timeout_ms=MOVE_TIMEOUT_MS, tolerance_deg=POSITION_TOLERANCE_DEG
            )
            time.sleep(MOTOR_SETTLE_S)

            if DARK_SUBTRACT:
                if not dry_run and not no_prompt:
                    input("Block the beam (or close shutter) for a dark frame, then press Enter: ")
                _capture_or_simulate(
                    camera, run_dir / "Images" / target / "dark.tiff", CAMERA_RETRIES, dry_run,
                    (lambda: bench.NOISE_FLOOR * 0.2) if dry_run else None,
                )
                if not dry_run and not no_prompt:
                    input("Unblock the beam, then press Enter to continue: ")

            _measure_calibration_angles(target, active_calibration_angles, run_dir, devices, camera, dry_run, bench, effective_zero_offsets)
            captured_targets.append(target)

            # Reverse of the just-in-time connect above: home this
            # compensator back to its reference position and disconnect it
            # before moving on, so it's not left under power/USB while the
            # operator physically removes it (or starts on the next QWP).
            print(f"[{target}] Homing before disconnect ...")
            devices[target].home_with_speed(HOME_SPEED_DEG_S, MOVE_TIMEOUT_MS)
            devices.pop(target).disconnect()
            print(f"[{target}] Disconnected.")
            if not dry_run and not no_prompt:
                print(f"*** {target} may now be physically removed/disconnected. ***")

        experiment_config = {
            "targets": captured_targets,
            "angle_mode": angle_mode,
            "calibration_angles_deg": list(active_calibration_angles),
            "null_search_mode": null_mode,
            "dry_run": dry_run,
            "dark_subtracted": DARK_SUBTRACT,
            "camera_model": camera.model,
            "camera_serial_number": camera.serial_number,
            "camera_exposure_us": exposure_us,
            "camera_frame_rate_fps": frame_rate_fps,
            "camera_gain": gain,
            "camera_pixel_format": pixel_format,
            "motor_velocity_deg_s": move_velocity_deg_s,
            "motor_acceleration_deg_s2": move_acceleration_deg_s2,
            "assumed_zero_offsets_deg": dict(ZERO_OFFSETS_DEG),
            "discovered_zero_offsets_deg": discovered,
            "null_search": null_search_report,
            "timestamp": datetime.now().astimezone().isoformat(),
        }
        (run_dir / "Config").mkdir(parents=True, exist_ok=True)
        (run_dir / "Config" / "experiment_config.json").write_text(json.dumps(experiment_config, indent=2), encoding="utf-8")
        (run_dir / "Config" / "discovered_zero_offsets.json").write_text(
            json.dumps(
                {
                    "assumed": dict(ZERO_OFFSETS_DEG),
                    "discovered": discovered,
                    "diff": {axis: discovered[axis] - ZERO_OFFSETS_DEG[axis] for axis in discovered},
                },
                indent=2,
            ),
            encoding="utf-8",
        )
        print(f"\nAcquisition complete. Saved: {run_dir / 'Config' / 'experiment_config.json'}")
        return run_dir

    finally:
        if camera is not None:
            camera.close()
        # Whatever's still connected here is P/A (each QWP already homed
        # itself back and disconnected right after its own measurement,
        # above) -- or, on an early failure, whatever never got that far.
        for axis in reversed(active_axes):
            motor = devices.pop(axis, None)
            if motor is not None:
                print(f"[{axis}] Homing before disconnect ...")
                motor.home_with_speed(HOME_SPEED_DEG_S, MOVE_TIMEOUT_MS)
                motor.disconnect()
                print(f"[{axis}] Disconnected.")


def run_reconstruction(run_dir: "Path | str", aggregation: str = AGGREGATION) -> int:
    """Hauge Sec. II's math half only: reads Config/experiment_config.json
    + Images/ (written by run_acquisition, above -- real hardware or
    dry-run alike) and runs the same closed-form (or N-angle least-
    squares) solve run_calibration always has, per captured target,
    entirely from a saved folder -- no hardware, no motors, no camera.
    Writes Config/calibration_result.json + per-pixel .npy maps + the
    delta_deg preview TIFF, identical shape to before this module was
    split (MMIE_ATOMIC_TARGETS.md target 2.8).

    qwp_calibration_reconstruction.py is this function's own CLI entry
    point, taking a run directory the same way discrete_reconstruction.py
    already does for Section III."""

    import numpy as np
    from PIL import Image

    run_dir = Path(run_dir)
    config = json.loads((run_dir / "Config" / "experiment_config.json").read_text(encoding="utf-8"))
    targets = config["targets"]
    angle_mode = config["angle_mode"]
    active_calibration_angles = tuple(config["calibration_angles_deg"])
    dark_subtracted = config["dark_subtracted"]
    discovered = config["discovered_zero_offsets_deg"]
    null_search_report = config["null_search"]

    combined_targets_report: dict = {}

    for target in targets:
        dark = None
        if dark_subtracted:
            dark_path = run_dir / "Images" / target / "dark.tiff"
            if dark_path.is_file():
                dark = np.asarray(Image.open(dark_path), dtype="float64")

        frames = {}
        for angle in active_calibration_angles:
            image = np.asarray(Image.open(run_dir / "Images" / target / f"C_{angle:g}.tiff"), dtype="float64")
            if dark is not None:
                image = image - dark
            frames[float(angle)] = image

        if angle_mode == "three_angle":
            print(f"Computing per-pixel calibration for {target} (Hauge Eq. 19-21, Eq. 4, minimal 3-point solve) ...")
            maps = compute_calibration(frames)
        else:
            print(
                f"Computing per-pixel calibration for {target} "
                f"({len(active_calibration_angles)}-angle least-squares fit, Eq. 21 generalized) ..."
            )
            maps = fit_calibration_least_squares(frames)
            diagnostics = maps["diagnostics"]
            if diagnostics["B2"] is not None:
                b2_max = float(np.nanmax(np.abs(diagnostics["B2"])))
                b4_max = float(np.nanmax(np.abs(diagnostics["B4"])))
                print(f"  Diagnostic (should be ~0 if P/A are at their assumed 0/90 reference): max|B2|={b2_max:.4f}, max|B4|={b4_max:.4f}")
            else:
                print(f"  (B2/B4 alignment diagnostic needs >= 5 angles; got {len(active_calibration_angles)}, skipped.)")

        target_roi = ROI
        if target_roi is None:
            reference = frames[float(active_calibration_angles[0])]
            ref8 = reference.astype("uint8") if reference.max() <= 255 else reference
            try:
                target_roi = select_roi(ref8, ROI_WINDOW_SIZE, ROI_STRIDE, ROI_MIN_MEAN)
            except CameraError:
                # Fall back to the A-vs-P ROI (bright_reference.tiff), same
                # as run_calibration always has, if this frame won't yield
                # its own flat-enough region.
                bright = np.asarray(Image.open(run_dir / "Images" / "bright_reference.tiff"))
                bright8 = bright.astype("uint8") if bright.max() <= 255 else bright
                target_roi = select_roi(bright8, ROI_WINDOW_SIZE, ROI_STRIDE, ROI_MIN_MEAN)

        summary = summarize_roi(maps, target_roi, aggregation)
        warnings = sanity_check_warnings(summary, aggregation)
        # Sanity check (not a Hauge formula, an engineering addition): this
        # compensator's own null shouldn't read much brighter than the
        # bare crossed-P/A null it was found against -- see
        # null_intensity_mismatch_warning's docstring for the physical
        # reasoning. Folded into `warnings` so it goes through the same
        # single print/storage path as the s/f/delta_deg checks.
        null_intensity_warning = null_intensity_mismatch_warning(
            null_search_report[target]["pa_null_intensity"], null_search_report[target]["compensator_null_intensity"], target,
        )
        if null_intensity_warning:
            warnings.append(null_intensity_warning)
        for warning in warnings:
            print(f"  SANITY WARNING [{target}]: {warning}")

        results_dir = run_dir / "Results" / target
        results_dir.mkdir(parents=True, exist_ok=True)
        for key in ("s", "f", "p", "r", "delta_deg", "T"):
            np.save(results_dir / f"{key}_map.npy", maps[key])
        if angle_mode == "least_squares" and maps["diagnostics"]["B2"] is not None:
            np.save(results_dir / "B2_map.npy", maps["diagnostics"]["B2"])
            np.save(results_dir / "B4_map.npy", maps["diagnostics"]["B4"])

        delta_map = maps["delta_deg"]
        valid = np.isfinite(delta_map)
        if valid.any():
            lo, hi = float(np.nanmin(delta_map)), float(np.nanmax(delta_map))
            span = hi - lo if hi > lo else 1.0
            scaled = np.clip((delta_map - lo) / span * 255.0, 0, 255)
            scaled = np.nan_to_num(scaled, nan=0.0).astype("uint8")
            Image.fromarray(scaled).save(results_dir / "delta_deg_map_preview.tiff")

        print(f"\n--- {target} summary ---")
        for key in ("s", "f", "p", "r", "delta_deg", "T"):
            val = summary[key][aggregation]
            std = summary[key]["std"]
            print(f"  {key:10s} = {val: .5f}  (std {std:.5f})")

        combined_targets_report[target] = {
            "angle_mode": angle_mode,
            "calibration_angles_deg": list(active_calibration_angles),
            "roi": {"x": target_roi[0], "y": target_roi[1], "width": target_roi[2], "height": target_roi[3]},
            "summary": summary,
            "n_unphysical_pixels": maps["n_unphysical_pixels"],
            "null_search": null_search_report[target],
            "sanity_warnings": warnings,
            "per_pixel_maps": {
                key: str((results_dir / f"{key}_map.npy").relative_to(run_dir)) for key in
                ("s", "f", "p", "r", "delta_deg", "T")
            },
        }
        if angle_mode == "least_squares":
            least_squares_diagnostics = {"num_angles": maps["diagnostics"]["num_angles"]}
            if maps["diagnostics"]["B2"] is not None:
                b2_region = maps["diagnostics"]["B2"][
                    target_roi[1] : target_roi[1] + target_roi[3], target_roi[0] : target_roi[0] + target_roi[2]
                ]
                b4_region = maps["diagnostics"]["B4"][
                    target_roi[1] : target_roi[1] + target_roi[3], target_roi[0] : target_roi[0] + target_roi[2]
                ]
                least_squares_diagnostics["B2_roi_max_abs"] = float(np.nanmax(np.abs(b2_region)))
                least_squares_diagnostics["B4_roi_max_abs"] = float(np.nanmax(np.abs(b4_region)))
            combined_targets_report[target]["least_squares_diagnostics"] = least_squares_diagnostics

    report = {
        "targets": list(targets),
        "timestamp": datetime.now().astimezone().isoformat(),
        "aggregation": aggregation,
        "dark_subtracted": dark_subtracted,
        "null_search_mode": config["null_search_mode"],
        "assumed_zero_offsets_deg": config["assumed_zero_offsets_deg"],
        "discovered_zero_offsets_deg": discovered,
        "results": combined_targets_report,
    }
    (run_dir / "Config" / "calibration_result.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(f"\nSaved: {run_dir / 'Config' / 'calibration_result.json'}")
    return 0


def run_calibration(
    targets: tuple[str, ...],
    null_mode: str,
    dry_run: bool,
    no_prompt: bool,
    angle_mode: str = CALIBRATION_ANGLE_MODE,
    num_least_squares_angles: int = LEAST_SQUARES_NUM_ANGLES,
) -> int:
    """The original one-shot combined flow: acquisition immediately
    followed by reconstruction, exactly as this function always behaved
    -- now genuinely round-tripping through Config/experiment_config.json
    and Images/ on disk between the two phases instead of an in-memory
    handoff (`qwp_calibration_capture.py`/`qwp_calibration_reconstruction.py`
    are the same two phases run as two separate scripts/processes)."""

    run_dir = run_acquisition(targets, null_mode, dry_run, no_prompt, angle_mode, num_least_squares_angles)
    if run_dir is None:
        return 1
    return run_reconstruction(run_dir, AGGREGATION)


# ============================================================================
# Entry point
# ============================================================================

def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Hauge (1978) QWP null-search + retardance calibration")
    parser.add_argument(
        "--target", choices=tuple(QWP_ROLES) + ("both",), default="both",
        help="Which QWP(s) to calibrate. 'both' (default) runs PSG_QWP then PSA_QWP back-to-back.",
    )
    parser.add_argument(
        "--null-search-mode", choices=("automated", "interactive"), default=NULL_SEARCH_MODE,
        help="Step-0 null-search strategy. 'automated' (default) is coarse-grid + golden-section; "
        "'interactive' is a manual nudge-and-confirm fallback.",
    )
    parser.add_argument(
        "--calibration-angle-mode", choices=("three_angle", "least_squares"), default=CALIBRATION_ANGLE_MODE,
        help="'three_angle' (default): Hauge's minimal exact solve at 0/45/90 deg. 'least_squares': "
        "fit the same closed-form relations across --num-calibration-angles evenly-spaced angles instead "
        "-- more captures, lower noise on s,f (and r,delta,T that derive from them).",
    )
    parser.add_argument(
        "--num-calibration-angles", type=int, default=LEAST_SQUARES_NUM_ANGLES,
        help="Number of angles for --calibration-angle-mode least_squares (ignored otherwise). Must be >= 3.",
    )
    parser.add_argument(
        "--dry-run", action="store_true", default=None,
        help="Simulate every motor/camera call, no hardware needed. If neither --dry-run nor "
        "--no-dry-run is given, you'll be prompted for it interactively (unless --no-prompt is "
        f"also set, in which case it falls back to DRY_RUN={DRY_RUN} from the script).",
    )
    parser.add_argument(
        "--no-dry-run", dest="dry_run", action="store_false",
        help="Explicitly run against real hardware, skipping the interactive dry-run prompt.",
    )
    parser.add_argument("--no-prompt", action="store_true", help="Skip confirmation prompts.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    dry_run = args.dry_run
    if dry_run is None:
        if args.no_prompt:
            dry_run = DRY_RUN
        else:
            dry_run = ask_yes_no(
                "Run in DRY-RUN mode (simulated motors/camera, no hardware needed)?", default=True
            )
    targets = tuple(QWP_ROLES) if args.target == "both" else (args.target,)
    try:
        return run_calibration(
            targets, args.null_search_mode, dry_run, args.no_prompt,
            args.calibration_angle_mode, args.num_calibration_angles,
        )
    except (MotorError, CameraError, ValueError, RuntimeError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
