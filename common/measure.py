"""MMIE Mueller-matrix acquisition -- one file, every mode.

Edit the "USER SETTINGS" block below, then run:

    python measure.py

or override the important bits from the command line for a one-off/
scripted run:

    python measure.py --mode 3x4 --acquisition discrete --run-label sample1
    python measure.py --mode 4x4 --acquisition continuous --dry-run --no-prompt

Covers 3x3/3x4/4x3/4x4 discrete-angle acquisition and 3x4/4x3/4x4
continuous-rotation acquisition (there is no 3x3 continuous mode -- a
continuous sweep needs at least one QWP to spin). Motor/camera
communication live in motor_communication.py / camera_communication.py;
this file only knows the experiment shape.

IMPORTANT -- output schema is load-bearing. Everything under
control/matrix/ (the offline reconstruction pipelines, 8 already-built
mode folders) reads a specific on-disk layout: Images/<first>_<second>.ext
filenames, Config/experiment_config.json with "mode"/"fixed_angles",
Logs/experiment_log.csv with fixed columns for continuous runs, and a
Dark/ folder (or Results/DarkReference_*.bmp) for dark-current
subtraction. This file writes exactly that layout -- do not change
write_config()/filenames/CSV columns without also checking every
control/matrix/*/image_loader.py that reads them.

Physics/naming reference (see control/matrix/*/README.md for the full
"from zero" writeup):

    Mode | first angle (filename prefix) | second angle (filename suffix) | fixed
    3x3  | PSG_Polarizer                  | PSA_Analyzer                    | none
    3x4  | PSG_QWP                        | PSA_Analyzer                    | PSG_Polarizer
    4x3  | PSG_Polarizer                  | PSA_QWP                         | PSA_Analyzer
    4x4  | PSG_QWP                        | PSA_QWP                         | PSG_Polarizer, PSA_Analyzer
"""

from __future__ import annotations

import argparse
import builtins
import csv
import importlib.util
import json
import os
import shutil
import signal
import sys
import threading
import time
import traceback
from datetime import datetime
from io import TextIOBase
from itertools import product
from pathlib import Path

from camera_communication import CameraError, IDSCamera, roi_mean, select_roi
from motor_communication import CageRotatorMotor, MotorError


# ============================================================================
# USER SETTINGS -- edit these before each measurement
# ============================================================================

MODE = "3x4"  # "3x3", "3x4", "4x3", or "4x4"
ACQUISITION_TYPE = "discrete"  # "discrete" or "continuous" (3x3 is discrete-only)

RUN_LABEL = "sample"  # Data/YYYY-MM-DD_<RUN_LABEL>_NN/ is created automatically

# --- discrete-only: the grid of angles named in the filename ---
FIRST_ANGLES_DEG = [0, 30, 60, 90, 120, 150]
SECOND_ANGLES_DEG = [0, 30, 60, 90, 120, 150]

# --- continuous-only ---
ROTATION_RATIO = (1, 5)  # 4x4 only: (slow, fast) PSG_QWP:PSA_QWP revolution ratio
OUTER_ANGLES_DEG = [0.0, 45.0, 90.0]  # 3x4/4x3 only: linear-only side, stepped between revolutions
CAPTURE_ANGLE_STEP_DEG = 1.0  # deg of QWP travel between captures (360/step frames per revolution)

# 3x4/4x4 use a fixed PSG polarizer; 4x3/4x4 use a fixed PSA polarizer.
FIXED_PSG_POL_DEG = 0.0
FIXED_PSA_POL_DEG = 0.0

# Fill in from each rotator's serial-number sticker; only the axes the
# selected mode actually needs are ever connected.
MOTOR_SERIALS = {
    "PSG_Polarizer": "55542004",
    "PSG_QWP": "55542914",
    "PSA_QWP": "55542224",
    "PSA_Analyzer": "55542504",
}

# motor_angle = (optical_angle + zero_offset) % 360.
ZERO_OFFSETS_DEG = {
    "PSG_Polarizer": 121.7,
    "PSG_QWP": 66.4,
    "PSA_QWP": 174.07,
    "PSA_Analyzer": 45.02,
}

# --- camera ---
CAMERA_EXPOSURE_US = 390
CAMERA_FRAME_RATE_FPS = 34.92
CAMERA_GAIN = 1.0
CAMERA_PIXEL_FORMAT = "Mono8"
CAMERA_TIMEOUT_MS = 5000
CAMERA_RETRIES = 2
CAMERA_MEAN_TOO_DARK = 1.0
CAMERA_MEAN_TOO_BRIGHT = 250.0

# --- motors ---
HOME_MOTORS = True
HOME_SPEED_DEG_S = 10.0
MOVE_VELOCITY_DEG_S = 10.0
MOVE_ACCELERATION_DEG_S2 = 20.0
MOVE_TIMEOUT_MS = 60_000
POSITION_TOLERANCE_DEG = 0.1
MOTOR_SETTLE_BEFORE_S = 0.5
MOTOR_SETTLE_AFTER_S = 0.2
MOTOR_MAX_RETRIES = 2
MOTOR_RETRY_BACKOFF_S = 1.0

# --- automatic bright/dark reference verification (once per sample) ---
ROI_WINDOW_SIZE = 200
ROI_STRIDE = 100
ROI_MIN_MEAN = 50.0

# --- safety: reject a discrete angle grid that can't reconstruct a
# full-rank Mueller matrix, before spending time capturing it ---
CHECK_ANGLE_GRID_RANK = True
QWP_RETARDANCE_DEG = 90.0  # only used by the pre-flight rank check's ideal-optics model

DRY_RUN = False


# ============================================================================
# Mode definitions -- shouldn't normally need editing
# ============================================================================

SCRIPT_DIR = Path(__file__).resolve().parent
# This file lives in common/, one level below the project root -- anchor
# Data/ to the root (not common/) so every section's output lands in the
# same shared top-level place regardless of which folder you run from.
DATA_ROOT = SCRIPT_DIR.parent / "Data"

MODE_DEFINITIONS = {
    "3x3": {
        "first_axis": "PSG_Polarizer",
        "second_axis": "PSA_Analyzer",
        "active_axes": ("PSG_Polarizer", "PSA_Analyzer"),
        "fixed_angle_keys": (),
        "acquisition_types": ("discrete",),
        "continuous_rotating_axes": (),
        "continuous_outer_axis": None,
        "matrix_shape": (3, 3),
    },
    "3x4": {
        "first_axis": "PSG_QWP",
        "second_axis": "PSA_Analyzer",
        "active_axes": ("PSG_Polarizer", "PSG_QWP", "PSA_Analyzer"),
        "fixed_angle_keys": ("PSG_Polarizer",),
        "acquisition_types": ("discrete", "continuous"),
        "continuous_rotating_axes": ("PSG_QWP",),
        "continuous_outer_axis": "PSA_Analyzer",
        "matrix_shape": (3, 4),
    },
    "4x3": {
        "first_axis": "PSG_Polarizer",
        "second_axis": "PSA_QWP",
        "active_axes": ("PSG_Polarizer", "PSA_QWP", "PSA_Analyzer"),
        "fixed_angle_keys": ("PSA_Analyzer",),
        "acquisition_types": ("discrete", "continuous"),
        "continuous_rotating_axes": ("PSA_QWP",),
        "continuous_outer_axis": "PSG_Polarizer",
        "matrix_shape": (4, 3),
    },
    "4x4": {
        "first_axis": "PSG_QWP",
        "second_axis": "PSA_QWP",
        "active_axes": ("PSG_Polarizer", "PSG_QWP", "PSA_QWP", "PSA_Analyzer"),
        "fixed_angle_keys": ("PSG_Polarizer", "PSA_Analyzer"),
        "acquisition_types": ("discrete", "continuous"),
        "continuous_rotating_axes": ("PSG_QWP", "PSA_QWP"),
        "continuous_outer_axis": None,
        "matrix_shape": (4, 4),
    },
}


def fixed_angles_for_mode(mode: str) -> dict[str, float]:
    values = {"PSG_Polarizer": FIXED_PSG_POL_DEG, "PSA_Analyzer": FIXED_PSA_POL_DEG}
    return {key: values[key] for key in MODE_DEFINITIONS[mode]["fixed_angle_keys"]}


# ============================================================================
# Small shared helpers
# ============================================================================

def optical_to_motor(axis: str, optical_deg: float) -> float:
    return (float(optical_deg) + float(ZERO_OFFSETS_DEG[axis])) % 360.0


def angle_text(angle: float) -> str:
    value = float(angle)
    if not (-3600.0 < value < 3600.0):
        raise ValueError(f"Angle out of reasonable range: {angle}")
    return f"{value:g}"


def write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def validate_settings(mode: str, acquisition_type: str) -> None:
    if mode not in MODE_DEFINITIONS:
        raise ValueError(f"MODE must be one of {tuple(MODE_DEFINITIONS)}, got {mode!r}.")
    definition = MODE_DEFINITIONS[mode]
    if acquisition_type not in definition["acquisition_types"]:
        raise ValueError(
            f"Mode {mode} supports acquisition type(s) {definition['acquisition_types']}, "
            f"not {acquisition_type!r}."
        )
    if acquisition_type == "discrete":
        if not FIRST_ANGLES_DEG or not SECOND_ANGLES_DEG:
            raise ValueError("FIRST_ANGLES_DEG and SECOND_ANGLES_DEG must not be empty.")
        first_names = [angle_text(v) for v in FIRST_ANGLES_DEG]
        second_names = [angle_text(v) for v in SECOND_ANGLES_DEG]
        if len(first_names) != len(set(first_names)) or len(second_names) != len(set(second_names)):
            raise ValueError("Angle lists contain duplicates -- this would overwrite TIFF filenames.")
    else:
        outer_axis = definition["continuous_outer_axis"]
        if outer_axis is not None and not OUTER_ANGLES_DEG:
            raise ValueError(f"OUTER_ANGLES_DEG must not be empty for mode {mode} continuous.")
    for axis in definition["active_axes"]:
        if not MOTOR_SERIALS.get(axis):
            raise ValueError(f"No motor serial number configured for {axis}.")
        if axis not in ZERO_OFFSETS_DEG:
            raise ValueError(f"No zero offset configured for {axis}.")


def check_environment() -> list[tuple[str, bool, str]]:
    """Import/filesystem-only diagnostics -- never touches real hardware,
    safe to run in dry-run. Drives the dry-run default suggestion."""

    checks: list[tuple[str, bool, str]] = []
    checks.append(("Python >= 3.11", sys.version_info >= (3, 11), sys.version.split()[0]))
    for package, import_name in (
        ("pythonnet", "clr"),
        ("IDS Peak", "ids_peak"),
        ("NumPy", "numpy"),
        ("Pillow", "PIL"),
    ):
        found = importlib.util.find_spec(import_name) is not None
        checks.append((package, found, "available" if found else "not importable"))
    from motor_communication import KINESIS_DIR

    checks.append(("Kinesis directory", KINESIS_DIR.is_dir(), str(KINESIS_DIR)))
    DATA_ROOT.mkdir(parents=True, exist_ok=True)
    checks.append(("Data directory writable", os.access(DATA_ROOT, os.W_OK), str(DATA_ROOT.resolve())))
    free_gb = shutil.disk_usage(DATA_ROOT).free / 1024**3
    checks.append(("Free disk >= 1 GB", free_gb >= 1.0, f"{free_gb:.2f} GB"))
    return checks


def make_run_directory(label: str) -> Path:
    safe_label = "_".join(label.strip().split()) or "sample"
    if any(char in safe_label for char in '<>:"/\\|?*'):
        raise ValueError(f"RUN_LABEL contains a character Windows doesn't allow: {label!r}")
    DATA_ROOT.mkdir(parents=True, exist_ok=True)
    date_text = datetime.now().strftime("%Y-%m-%d")
    for index in range(1, 1000):
        run_dir = DATA_ROOT / f"{date_text}_{safe_label}_{index:02d}"
        try:
            run_dir.mkdir()
            (run_dir / "Images").mkdir()
            return run_dir
        except FileExistsError:
            continue
    raise RuntimeError("More than 999 same-day, same-label run folders already exist.")


def ask_yes_no(prompt: str, default: bool = False) -> bool:
    suffix = " [Y/n]: " if default else " [y/N]: "
    answer = input(prompt + suffix).strip().lower()
    return default if not answer else answer in {"y", "yes"}


# ============================================================================
# Pre-flight angle-grid rank check (discrete only)
#
# Duplicated, deliberately minimal ideal-optics formulas -- same
# "each consumer owns its own physics copy" convention already used
# throughout control/matrix/. This is NOT the reconstruction; it only
# answers "would this planned grid even be solvable," before capture.
# ============================================================================

def _ideal_linear_state(angle_deg: float):
    import numpy as np

    t = np.deg2rad(angle_deg)
    return np.array([1.0, np.cos(2 * t), np.sin(2 * t), 0.0])


def _ideal_linear_retarder(angle_deg: float, retardance_deg: float):
    import numpy as np

    t = np.deg2rad(angle_deg)
    delta = np.deg2rad(retardance_deg)
    c2, s2 = np.cos(2 * t), np.sin(2 * t)
    cd, sd = np.cos(delta), np.sin(delta)
    return np.array(
        [
            [1.0, 0.0, 0.0, 0.0],
            [0.0, c2 * c2 + s2 * s2 * cd, c2 * s2 * (1 - cd), -s2 * sd],
            [0.0, c2 * s2 * (1 - cd), s2 * s2 + c2 * c2 * cd, c2 * sd],
            [0.0, s2 * sd, -c2 * sd, cd],
        ]
    )


def _ideal_generator_vector(mode: str, first_angle: float, fixed: dict, retardance_deg: float):
    if mode in ("3x3", "4x3"):
        return _ideal_linear_state(first_angle)[:3]
    return _ideal_linear_retarder(first_angle, retardance_deg) @ _ideal_linear_state(
        fixed.get("PSG_Polarizer", 0.0)
    )


def _ideal_analyzer_vector(mode: str, second_angle: float, fixed: dict, retardance_deg: float):
    if mode in ("3x3", "3x4"):
        return _ideal_linear_state(second_angle)[:3]
    return _ideal_linear_state(fixed.get("PSA_Analyzer", 0.0)) @ _ideal_linear_retarder(
        second_angle, retardance_deg
    )


def check_angle_grid_rank(mode: str, first_angles, second_angles, fixed: dict, retardance_deg: float) -> None:
    """Raise ValueError if the planned (first, second) angle grid would
    give a rank-deficient reconstruction system -- catches a degenerate
    grid (e.g. QWP angles spaced 90 deg apart, which alias in the
    cos(2*theta)/sin(2*theta) terms) before any image is captured."""

    import numpy as np

    rows = []
    for first, second in product(first_angles, second_angles):
        a = _ideal_analyzer_vector(mode, second, fixed, retardance_deg)
        s = _ideal_generator_vector(mode, first, fixed, retardance_deg)
        rows.append(np.kron(a, s))
    matrix = np.asarray(rows, dtype=np.float64)
    rows_needed, cols_needed = MODE_DEFINITIONS[mode]["matrix_shape"]
    required_rank = rows_needed * cols_needed
    rank = int(np.linalg.matrix_rank(matrix))
    if rank < required_rank:
        raise ValueError(
            f"Planned {mode} angle grid gives system rank={rank}, below the {required_rank} "
            f"needed for a full {rows_needed}x{cols_needed} reconstruction. Add or spread out "
            f"FIRST_ANGLES_DEG/SECOND_ANGLES_DEG (avoid angles 90 deg apart for a rotating QWP "
            "-- that spacing aliases in cos(2*theta)/sin(2*theta) and loses rank)."
        )
    print(f"Pre-flight rank check: system rank {rank}/{required_rank} -- OK.")


# ============================================================================
# Session transcript -- tee stdout/stderr/input to Logs/terminal_transcript.txt
# ============================================================================

class _TeeStream(TextIOBase):
    def __init__(self, original, log_handle, lock: threading.Lock) -> None:
        self.original = original
        self.log_handle = log_handle
        self.lock = lock

    def write(self, text: str) -> int:
        with self.lock:
            self.original.write(text)
            self.log_handle.write(text)
            self.original.flush()
            self.log_handle.flush()
        return len(text)

    def flush(self) -> None:
        with self.lock:
            self.original.flush()
            self.log_handle.flush()

    def isatty(self) -> bool:
        return self.original.isatty()


class SessionTranscript:
    """Records every print() and every operator answer to
    Logs/terminal_transcript.txt for the duration it's started."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self._handle = None
        self._original_stdout = sys.stdout
        self._original_stderr = sys.stderr
        self._original_input = builtins.input
        self._lock = threading.Lock()

    def start(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._handle = self.path.open("a", encoding="utf-8", buffering=1)
        self._handle.write(f"\n{'=' * 72}\nSESSION START {datetime.now().astimezone().isoformat()}\n{'=' * 72}\n")
        self._handle.flush()
        sys.stdout = _TeeStream(self._original_stdout, self._handle, self._lock)
        sys.stderr = _TeeStream(self._original_stderr, self._handle, self._lock)

        def audited_input(prompt: str = "") -> str:
            if prompt:
                print(prompt, end="", flush=True)
            answer = self._original_input("")
            with self._lock:
                self._handle.write(f"[OPERATOR INPUT] {answer}\n")
                self._handle.flush()
            return answer

        builtins.input = audited_input

    def stop(self) -> None:
        if self._handle is None:
            return
        print(f"SESSION END {datetime.now().astimezone().isoformat()}")
        builtins.input = self._original_input
        sys.stdout = self._original_stdout
        sys.stderr = self._original_stderr
        self._handle.close()
        self._handle = None


# ============================================================================
# Checkpointing (discrete only -- continuous acquisition has never had
# resume: an interrupted revolution/outer-step just restarts from scratch)
# ============================================================================

class CheckpointManager:
    def __init__(self, path: Path) -> None:
        self.path = path

    def load(self) -> dict:
        if not self.path.exists():
            return {"last_completed_index": -1, "experiment_completed": False}
        return json.loads(self.path.read_text(encoding="utf-8"))

    def next_index(self) -> int:
        return int(self.load().get("last_completed_index", -1)) + 1

    def update(self, index: int, filename: str, optical_angles: dict) -> None:
        write_json(
            self.path,
            {
                "last_completed_index": index,
                "filename": filename,
                "optical_angles": optical_angles,
                "experiment_completed": False,
            },
        )

    def complete(self, total_states: int) -> None:
        payload = self.load()
        payload.update({"total_states": total_states, "experiment_completed": True})
        write_json(self.path, payload)


# ============================================================================
# Motor set -- opens/homes the active axes for this mode, and moves only
# whichever axes actually changed value (fixed axes are parked once,
# before the loop, and never re-commanded every state).
# ============================================================================

class MotorSet:
    def __init__(self, axes: tuple[str, ...], dry_run: bool) -> None:
        self.axes = axes
        self.dry_run = dry_run
        self.devices: dict[str, CageRotatorMotor] = {}

    def open(self) -> None:
        for axis in self.axes:
            print(f"[{axis}] Connecting motor {MOTOR_SERIALS[axis]} ...")
            motor = CageRotatorMotor(MOTOR_SERIALS[axis], self.dry_run)
            motor.connect()
            motor.set_velocity(MOVE_VELOCITY_DEG_S, MOVE_ACCELERATION_DEG_S2)
            self.devices[axis] = motor
            if HOME_MOTORS:
                print(f"[{axis}] Homing ...")
                motor.home_with_speed(HOME_SPEED_DEG_S, MOVE_TIMEOUT_MS)
            elif motor.is_homed_high_level() is not True:
                raise MotorError(
                    f"{axis} is not homed; set HOME_MOTORS = True, or home it in Kinesis first."
                )
        print(f"All motors ready: {', '.join(self.axes)}.")

    def move_optical(self, optical_angles: dict[str, float]) -> dict[str, float]:
        """Move every axis present in optical_angles to that OPTICAL angle,
        retrying on a tolerance failure. Axes not present are left alone."""

        reported = {}
        for axis, optical in optical_angles.items():
            if axis not in self.devices:
                continue
            target = optical_to_motor(axis, float(optical))
            print(f"  {axis}: optical {float(optical):g}° -> motor {target:.4f}°")
            last_error: Exception | None = None
            for attempt in range(1, MOTOR_MAX_RETRIES + 2):
                try:
                    reported[axis] = self.devices[axis].move_cage_rotator_to(
                        target, timeout_ms=MOVE_TIMEOUT_MS, tolerance_deg=POSITION_TOLERANCE_DEG
                    )
                    break
                except MotorError as exc:
                    last_error = exc
                    print(f"  {axis} move attempt {attempt}/{MOTOR_MAX_RETRIES + 1} failed: {exc}")
                    if attempt <= MOTOR_MAX_RETRIES:
                        time.sleep(MOTOR_RETRY_BACKOFF_S)
            else:
                raise MotorError(f"{axis} move failed after {MOTOR_MAX_RETRIES + 1} attempts: {last_error}")
        if optical_angles:
            time.sleep(MOTOR_SETTLE_BEFORE_S)
        return reported

    def move_motor_angle_raw(self, axis: str, motor_angle_deg: float) -> float:
        """Move directly to a MOTOR angle, bypassing the optical offset --
        used only by continuous mode to park a rotating QWP at a known
        raw-encoder starting point (0 deg) before a revolution."""

        return self.devices[axis].move_cage_rotator_to(
            motor_angle_deg, timeout_ms=MOVE_TIMEOUT_MS, tolerance_deg=POSITION_TOLERANCE_DEG
        )

    def encoder_positions(self) -> dict[str, float]:
        return {axis: motor.read_pos() for axis, motor in self.devices.items()}

    def set_axis_velocity(self, axis: str, velocity_deg_s: float, accel_deg_s2: float) -> None:
        self.devices[axis].set_velocity(velocity_deg_s, accel_deg_s2)

    def start_continuous(self, axis: str, forward: bool = True) -> None:
        self.devices[axis].start_continuous(forward)

    def stop_continuous(self, axis: str) -> None:
        self.devices[axis].stop_continuous(MOVE_TIMEOUT_MS)

    def stop(self) -> None:
        print("EMERGENCY STOP: stopping all connected motors.")
        for motor in self.devices.values():
            motor.stop()

    def close(self) -> None:
        for axis in reversed(self.axes):
            motor = self.devices.pop(axis, None)
            if motor is not None:
                motor.disconnect()
                print(f"[{axis}] Disconnected.")


# ============================================================================
# Automatic bright/dark reference capture + verification (once per sample)
# ============================================================================

def capture_camera_references(
    mode: str, run_dir: Path, motors: MotorSet, camera: IDSCamera, dry_run: bool, no_prompt: bool
) -> None:
    """PSA_Analyzer is active in every mode (fixed or itself sweeping), so
    this always works the same way: capture bright at whatever angle it's
    parked/starts at (its fixed angle for 4x3/4x4, else 0), capture dark
    90 deg away, restore, and verify bright > dark with no saturation."""

    definition = MODE_DEFINITIONS[mode]
    bright_angle = FIXED_PSA_POL_DEG if "PSA_Analyzer" in definition["fixed_angle_keys"] else 0.0
    reference_dir = run_dir / "Results"
    reference_dir.mkdir(parents=True, exist_ok=True)

    motors.move_optical({"PSA_Analyzer": bright_angle})
    if dry_run:
        camera.capture_tiff(reference_dir / "BrightReference.tiff")
        motors.move_optical({"PSA_Analyzer": (bright_angle + 90.0) % 360.0})
        camera.capture_tiff(reference_dir / "DarkReference.tiff")
        motors.move_optical({"PSA_Analyzer": bright_angle})
        print("Dry-run: reference files captured; physical contrast cannot be evaluated (no ROI selected).")
        return

    if not no_prompt:
        if not ask_yes_no("Illumination is ON? (required before the automatic bright/dark check)"):
            raise RuntimeError("Illumination not confirmed on; aborting before reference capture.")

    print(f"Capturing bright reference at PSA_Analyzer optical {bright_angle:g}° ...")
    camera.capture_tiff(reference_dir / "BrightReference.tiff")
    import numpy as np  # noqa: F401  (ensures a clear error if NumPy is missing)

    bright_image = camera.acquire_array()
    roi = select_roi(bright_image, ROI_WINDOW_SIZE, ROI_STRIDE, ROI_MIN_MEAN)
    write_json(run_dir / "Config" / "roi.json", {"x": roi[0], "y": roi[1], "width": roi[2], "height": roi[3]})
    bright_mean = roi_mean(bright_image, roi)

    dark_angle = (bright_angle + 90.0) % 360.0
    motors.move_optical({"PSA_Analyzer": dark_angle})
    print(f"Capturing dark reference at PSA_Analyzer optical {dark_angle:g}° ...")
    camera.capture_tiff(reference_dir / "DarkReference.tiff")
    dark_image = camera.acquire_array()
    dark_mean = roi_mean(dark_image, roi)
    motors.move_optical({"PSA_Analyzer": bright_angle})

    contrast = float("inf") if dark_mean == 0 else bright_mean / dark_mean
    print(
        f"Reference result (ROI {roi[2]}x{roi[3]} at {roi[0]},{roi[1]}) -- "
        f"bright mean {bright_mean:.3f}, dark mean {dark_mean:.3f}, ratio {contrast:.3f}"
    )
    problems = []
    if bright_mean <= dark_mean:
        problems.append("bright ROI mean is not greater than dark ROI mean")
    if int((bright_image == 255).sum()) > 0:
        problems.append("bright reference contains saturated (255) pixels")
    if problems:
        print("CAMERA VERIFICATION WARNING: " + "; ".join(problems))
        if not no_prompt and not ask_yes_no("Continue despite the camera verification warning?"):
            raise RuntimeError("Camera verification failed and operator declined to continue.")
    else:
        print("Camera bright/dark and saturation verification passed.")


# ============================================================================
# Config / report writing -- schema matches control/matrix/'s loaders exactly
# ============================================================================

def write_config(
    run_dir: Path,
    mode: str,
    acquisition_type: str,
    fixed: dict,
    dry_run: bool,
    camera: IDSCamera,
    status: str,
    extra: dict | None = None,
) -> None:
    config = {
        "mode": mode,  # exact "3x3"/"3x4"/"4x3"/"4x4" -- control/matrix/*/image_loader.py checks this
        "acquisition_type": acquisition_type,
        "status": status,
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "run_directory": str(run_dir),
        "fixed_angles": {key: float(value) for key, value in fixed.items()},
        "motor_serials": dict(MOTOR_SERIALS),
        "zero_offsets_deg": dict(ZERO_OFFSETS_DEG),
        "camera": {
            "model": camera.model,
            "serial_number": camera.serial_number,
            "requested_exposure_us": float(CAMERA_EXPOSURE_US),
            "requested_frame_rate_fps": float(CAMERA_FRAME_RATE_FPS),
            "requested_gain": float(CAMERA_GAIN),
            "pixel_format": CAMERA_PIXEL_FORMAT,
            "applied": camera.applied,
        },
        "dry_run": bool(dry_run),
    }
    if extra:
        config.update(extra)
    write_json(run_dir / "Config" / "experiment_config.json", config)


def write_report(run_dir: Path, mode: str, acquisition_type: str, completed: int, failed: int, elapsed_s: float) -> None:
    lines = [
        "MMIE Experiment Report",
        "=" * 22,
        f"Mode: {mode} ({acquisition_type})",
        f"Total images: {completed}",
        f"Failed images: {failed}",
        f"Elapsed time (s): {elapsed_s:.3f}",
    ]
    (run_dir / "Reports").mkdir(parents=True, exist_ok=True)
    (run_dir / "Reports" / "ExperimentReport.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")


# ============================================================================
# Discrete acquisition
# ============================================================================

def build_discrete_states(mode: str, first_angles, second_angles, fixed: dict):
    definition = MODE_DEFINITIONS[mode]
    states = []
    for index, (first, second) in enumerate(product(first_angles, second_angles)):
        optical = dict(fixed)
        optical[definition["first_axis"]] = float(first)
        optical[definition["second_axis"]] = float(second)
        filename = f"{angle_text(first)}_{angle_text(second)}.tiff"
        states.append((index, optical, filename))
    return states


def run_discrete_acquisition(
    run_dir: Path,
    states: list,
    motors: MotorSet,
    camera: IDSCamera,
    checkpoint: CheckpointManager,
    stop_event: threading.Event,
) -> tuple[int, int]:
    log_path = run_dir / "Logs" / "measurement_log.csv"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    is_new = not log_path.exists()
    completed = failed = 0
    start_index = checkpoint.next_index()
    if start_index:
        print(f"Resuming at state {start_index + 1}; earlier states are checkpointed.")
    previous: dict[str, float] = {}

    with log_path.open("a", newline="", encoding="utf-8-sig") as handle:
        writer = csv.writer(handle)
        if is_new:
            writer.writerow(["timestamp", "index", "filename", "optical_angles", "mean_intensity", "status", "error"])

        for index, optical, filename in states[start_index:]:
            if stop_event.is_set():
                motors.stop()
                raise KeyboardInterrupt("Acquisition stopped by operator.")
            print(f"[{index + 1}/{len(states)}] {filename}")
            changed = {axis: angle for axis, angle in optical.items() if previous.get(axis) != angle}
            try:
                motors.move_optical(changed)
                previous.update(optical)
                if stop_event.is_set():
                    motors.stop()
                    raise KeyboardInterrupt("Acquisition stopped by operator.")
                stats = camera.capture_tiff(run_dir / "Images" / filename)
                writer.writerow(
                    [datetime.now().isoformat(timespec="seconds"), index, filename, json.dumps(optical), stats["mean"], "SUCCESS", ""]
                )
                handle.flush()
                checkpoint.update(index, filename, optical)
                completed += 1
            except (MotorError, CameraError) as exc:
                failed += 1
                writer.writerow([datetime.now().isoformat(timespec="seconds"), index, filename, json.dumps(optical), "", "FAILED", str(exc)])
                handle.flush()
                print(f"State failed: {filename}: {exc}")
                raise
            finally:
                time.sleep(MOTOR_SETTLE_AFTER_S)

    checkpoint.complete(len(states))
    return completed, failed


# ============================================================================
# Continuous acquisition -- angle-triggered capture, nested outer loop for
# the asymmetric 3x4/4x3 modes (see MODE_DEFINITIONS' continuous_outer_axis).
#
# Ported from continous_rotation/continuous_engine.py: a single spinning
# QWP against a genuinely FIXED linear analyzer/generator would only span a
# rank-4 subspace of the unknowns (not enough to recover all rows/columns),
# so the linear-only side is stepped through OUTER_ANGLES_DEG, with one
# full QWP revolution captured at EACH outer angle.
# ============================================================================

def _run_one_revolution(
    run_dir: Path,
    mode: str,
    rotating: list[str],
    motors: MotorSet,
    camera: IDSCamera,
    csv_writer,
    csv_handle,
    frame_index_start: int,
    outer_axis: str | None,
    outer_angle: float | None,
    stop_event: threading.Event,
) -> tuple[int, int, int]:
    images_dir = run_dir / "Images"
    primary = rotating[0]
    secondary = rotating[1] if len(rotating) == 2 else None

    for axis in rotating:
        motors.move_motor_angle_raw(axis, 0.0)

    if secondary is not None:
        slow_ratio, fast_ratio = ROTATION_RATIO
        fast_velocity = MOVE_VELOCITY_DEG_S * (fast_ratio / slow_ratio)
        motors.set_axis_velocity(primary, MOVE_VELOCITY_DEG_S, MOVE_ACCELERATION_DEG_S2)
        motors.set_axis_velocity(secondary, fast_velocity, MOVE_ACCELERATION_DEG_S2)
        print(f"Spin velocity: {primary} {MOVE_VELOCITY_DEG_S:.3f} deg/s, {secondary} {fast_velocity:.3f} deg/s.")
    else:
        motors.set_axis_velocity(primary, MOVE_VELOCITY_DEG_S, MOVE_ACCELERATION_DEG_S2)
        print(f"Spin velocity: {primary} {MOVE_VELOCITY_DEG_S:.3f} deg/s.")

    if stop_event.is_set():
        motors.stop()
        raise KeyboardInterrupt("Acquisition stopped by operator.")

    start_angle = motors.encoder_positions()[primary]
    for axis in rotating:
        motors.start_continuous(axis)

    frame_index = frame_index_start
    completed = failed = 0
    next_threshold_deg = 0.0
    total_frames = int(round(360.0 / CAPTURE_ANGLE_STEP_DEG))
    revolution_frame = 0
    outer_tag = f"outer{outer_angle:.1f}_" if outer_axis is not None else ""
    has_psg_qwp = "PSG_QWP" in rotating
    has_psa_qwp = "PSA_QWP" in rotating

    try:
        while revolution_frame < total_frames:
            if stop_event.is_set():
                motors.stop()
                raise KeyboardInterrupt("Acquisition stopped by operator.")
            positions = motors.encoder_positions()
            primary_angle = positions[primary]
            traveled_deg = (primary_angle - start_angle) % 360.0
            if traveled_deg >= next_threshold_deg:
                psg_angle = positions.get("PSG_QWP") if has_psg_qwp else outer_angle
                psa_angle = positions.get("PSA_QWP") if has_psa_qwp else outer_angle
                filename = f"frame_{frame_index:04d}_{outer_tag}psg{psg_angle:.1f}_psa{psa_angle:.1f}.tiff"
                try:
                    camera.capture_tiff(images_dir / filename)
                    csv_writer.writerow(
                        {
                            "Frame Index": frame_index,
                            "PSG_QWP Angle": psg_angle,
                            "PSA_QWP Angle": psa_angle,
                            "Timestamp": datetime.now().astimezone().isoformat(),
                            "Attempt Count": 1,
                            "Status": "SUCCESS",
                        }
                    )
                    csv_handle.flush()
                    completed += 1
                    print(f"[{revolution_frame + 1}/{total_frames}] Captured {filename}")
                except CameraError as exc:
                    failed += 1
                    csv_writer.writerow(
                        {
                            "Frame Index": frame_index,
                            "PSG_QWP Angle": psg_angle,
                            "PSA_QWP Angle": psa_angle,
                            "Timestamp": datetime.now().astimezone().isoformat(),
                            "Attempt Count": 1,
                            "Status": "FAILED",
                        }
                    )
                    csv_handle.flush()
                    print(f"[{revolution_frame + 1}/{total_frames}] Frame failed: {exc}")
                frame_index += 1
                revolution_frame += 1
                next_threshold_deg += CAPTURE_ANGLE_STEP_DEG
            time.sleep(0.05)
        return completed, failed, frame_index
    finally:
        for axis in rotating:
            try:
                motors.stop_continuous(axis)
            except Exception as exc:
                print(f"Warning: could not stop {axis} cleanly: {exc}")


def run_continuous_acquisition(
    run_dir: Path, mode: str, motors: MotorSet, camera: IDSCamera, stop_event: threading.Event
) -> tuple[int, int]:
    definition = MODE_DEFINITIONS[mode]
    rotating = list(definition["continuous_rotating_axes"])
    outer_axis = definition["continuous_outer_axis"]
    outer_angles = list(OUTER_ANGLES_DEG) if outer_axis else [None]
    (run_dir / "Images").mkdir(parents=True, exist_ok=True)

    csv_path = run_dir / "Logs" / "experiment_log.csv"
    csv_path.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = ("Frame Index", "PSG_QWP Angle", "PSA_QWP Angle", "Timestamp", "Attempt Count", "Status")

    total_completed = total_failed = 0
    frame_index = 0
    with csv_path.open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for outer_angle in outer_angles:
            if outer_axis is not None:
                motors.move_optical({outer_axis: outer_angle})
            completed, failed, frame_index = _run_one_revolution(
                run_dir, mode, rotating, motors, camera, writer, handle, frame_index, outer_axis, outer_angle, stop_event
            )
            total_completed += completed
            total_failed += failed
    return total_completed, total_failed


# ============================================================================
# Session orchestration
# ============================================================================

def run_fresh_session(mode: str, acquisition_type: str, run_label: str, dry_run: bool, no_prompt: bool) -> int:
    validate_settings(mode, acquisition_type)
    definition = MODE_DEFINITIONS[mode]
    fixed = fixed_angles_for_mode(mode)

    print("Environment verification")
    all_ok = True
    for name, passed, detail in check_environment():
        print(f"  {'OK' if passed else 'MISSING':7} {name}: {detail}")
        all_ok &= passed
    if not dry_run and not all_ok:
        print("Required production dependencies are missing; non-dry operation is unsafe.")
        return 2

    stop_event = threading.Event()
    motors = MotorSet(definition["active_axes"], dry_run)
    camera = IDSCamera(
        dry_run,
        CAMERA_EXPOSURE_US,
        CAMERA_FRAME_RATE_FPS,
        CAMERA_GAIN,
        CAMERA_PIXEL_FORMAT,
        CAMERA_TIMEOUT_MS,
        CAMERA_RETRIES,
        CAMERA_MEAN_TOO_DARK,
        CAMERA_MEAN_TOO_BRIGHT,
    )

    def request_stop(_signum, _frame) -> None:
        stop_event.set()
        motors.stop()

    signal.signal(signal.SIGINT, request_stop)

    motors.open()
    camera.open()

    first_sample = True
    try:
        while True:
            if no_prompt:
                label, operator, comments = run_label, "", ""
            else:
                operator = input("Operator name: ").strip()
                label = input(f"Sample name [{run_label}]: ").strip() or run_label
                comments = input("Comments: ").strip()

            run_dir = make_run_directory(label)
            transcript = SessionTranscript(run_dir / "Logs" / "terminal_transcript.txt")
            transcript.start()
            print(f"Mode: {mode} ({acquisition_type})")
            print(f"Fixed angles: {fixed or 'none'}")
            status = "initializing"
            extra: dict = {"metadata": {"operator": operator, "sample": label, "comments": comments}}
            try:
                (run_dir / "Config").mkdir(parents=True, exist_ok=True)
                if acquisition_type == "discrete":
                    if CHECK_ANGLE_GRID_RANK:
                        check_angle_grid_rank(mode, FIRST_ANGLES_DEG, SECOND_ANGLES_DEG, fixed, QWP_RETARDANCE_DEG)
                    states = build_discrete_states(mode, FIRST_ANGLES_DEG, SECOND_ANGLES_DEG, fixed)
                    extra["state_inputs"] = {
                        definition["first_axis"]: [float(v) for v in FIRST_ANGLES_DEG],
                        definition["second_axis"]: [float(v) for v in SECOND_ANGLES_DEG],
                    }
                    print(f"Total states: {len(states)}")
                else:
                    extra["outer_angles"] = list(OUTER_ANGLES_DEG) if definition["continuous_outer_axis"] else []
                    extra["rotation_ratio"] = list(ROTATION_RATIO)

                write_config(run_dir, mode, acquisition_type, fixed, dry_run, camera, status, extra)
                capture_camera_references(mode, run_dir, motors, camera, dry_run, no_prompt)

                if not no_prompt:
                    input("Insert the sample now, then press Enter to start acquisition: ")

                started = time.monotonic()
                if acquisition_type == "discrete":
                    checkpoint = CheckpointManager(run_dir / "Checkpoints" / "checkpoint.json")
                    completed, failed = run_discrete_acquisition(run_dir, states, motors, camera, checkpoint, stop_event)
                else:
                    if fixed:
                        motors.move_optical(fixed)
                    completed, failed = run_continuous_acquisition(run_dir, mode, motors, camera, stop_event)
                elapsed = time.monotonic() - started
                status = "completed"
                print(f"Measurement complete: {completed} images, {failed} failed.")
                write_report(run_dir, mode, acquisition_type, completed, failed, elapsed)
            except KeyboardInterrupt:
                status = "stopped_by_user"
                print("\nStopped by operator; motors have been stopped.")
                raise
            except Exception:
                status = "failed"
                (run_dir / "Logs").mkdir(parents=True, exist_ok=True)
                (run_dir / "Logs" / "error_traceback.txt").write_text(traceback.format_exc(), encoding="utf-8")
                print(traceback.format_exc())
                if no_prompt or not ask_yes_no("This sample failed. Continue with another sample?", default=False):
                    return 1
            finally:
                write_config(run_dir, mode, acquisition_type, fixed, dry_run, camera, status, extra)
                transcript.stop()

            first_sample = False
            if no_prompt:
                return 0
            if not ask_yes_no("Measure another sample?"):
                return 0
    except KeyboardInterrupt:
        return 130
    finally:
        camera.close()
        motors.close()


def resume_discrete_session(run_dir: Path) -> int:
    """--resume: recover exactly one interrupted discrete run from its
    checkpoint. No multi-sample loop -- rerun without --resume afterward
    for additional samples."""

    config_path = run_dir / "Config" / "experiment_config.json"
    if not config_path.is_file():
        raise FileNotFoundError(f"No saved configuration at {config_path}")
    config = json.loads(config_path.read_text(encoding="utf-8"))
    mode = config["mode"]
    if config.get("acquisition_type") != "discrete":
        raise ValueError("--resume only supports discrete-acquisition runs.")
    definition = MODE_DEFINITIONS[mode]
    fixed = {key: float(value) for key, value in config.get("fixed_angles", {}).items()}
    state_inputs = config["state_inputs"]
    first_values = state_inputs[definition["first_axis"]]
    second_values = state_inputs[definition["second_axis"]]
    states = build_discrete_states(mode, first_values, second_values, fixed)
    dry_run = bool(config.get("dry_run", False))

    print(f"Resuming saved {mode} discrete experiment: {run_dir}")
    if not ask_yes_no("Begin hardware initialization and acquisition?"):
        return 0

    stop_event = threading.Event()
    motors = MotorSet(definition["active_axes"], dry_run)
    camera = IDSCamera(
        dry_run, CAMERA_EXPOSURE_US, CAMERA_FRAME_RATE_FPS, CAMERA_GAIN, CAMERA_PIXEL_FORMAT,
        CAMERA_TIMEOUT_MS, CAMERA_RETRIES, CAMERA_MEAN_TOO_DARK, CAMERA_MEAN_TOO_BRIGHT,
    )

    def request_stop(_signum, _frame) -> None:
        stop_event.set()
        motors.stop()

    signal.signal(signal.SIGINT, request_stop)
    try:
        motors.open()
        camera.open()
        checkpoint = CheckpointManager(run_dir / "Checkpoints" / "checkpoint.json")
        completed, failed = run_discrete_acquisition(run_dir, states, motors, camera, checkpoint, stop_event)
        print(f"Resumed measurement complete: {completed} images, {failed} failed.")
        return 0
    except KeyboardInterrupt:
        return 130
    finally:
        camera.close()
        motors.close()


# ============================================================================
# Entry point
# ============================================================================

def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="MMIE Mueller-matrix acquisition")
    parser.add_argument("--mode", choices=tuple(MODE_DEFINITIONS), default=MODE)
    parser.add_argument("--acquisition", choices=("discrete", "continuous"), default=ACQUISITION_TYPE)
    parser.add_argument("--run-label", default=RUN_LABEL)
    parser.add_argument("--dry-run", action="store_true", default=DRY_RUN)
    parser.add_argument("--no-prompt", action="store_true", help="Skip confirmation prompts; one sample, then exit.")
    parser.add_argument("--resume", type=Path, default=None, metavar="RUN_DIRECTORY", help="Resume an interrupted discrete run.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if args.resume:
        return resume_discrete_session(args.resume.resolve())
    return run_fresh_session(args.mode, args.acquisition, args.run_label, args.dry_run, args.no_prompt)


if __name__ == "__main__":
    raise SystemExit(main())
