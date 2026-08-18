"""Thorlabs K10CR2/M motor communication -- one motor per instance.

Deliberately thin: this file only knows how to talk to a single rotation
stage via Kinesis/.NET (connect, home, move-to-angle, spin continuously,
stop, disconnect). Everything experiment-specific (which motors exist,
what angles to visit, retries, checkpoints, dark references, ...) lives in
measure.py. Kinesis/pythonnet is imported lazily, on first real (non-dry)
connect -- so dry-run needs neither installed, and importing this module
never touches the DLL search path or attempts a `clr` import by itself.
"""

from __future__ import annotations

import os
import sys
import time
from pathlib import Path


KINESIS_DIR = Path(r"C:\Program Files\Thorlabs\Kinesis")
MOTOR_SETTINGS_NAME = "K10CR2"
_DLL_DIRECTORY_HANDLE = None
_API = None


class MotorError(RuntimeError):
    """Raised when a motor cannot safely connect, move, or home.
    ``attempts`` records how many tries were actually made (retries are
    the caller's -- MotorSet's -- responsibility; a single MotorCommunication
    call either succeeds or raises once)."""

    def __init__(self, message: str, attempts: int = 1) -> None:
        super().__init__(message)
        self.attempts = attempts


def _load_kinesis_api() -> dict:
    """Load the Kinesis .NET assemblies exactly once, only when a real
    (non-dry-run) connection is actually attempted."""

    global _API, _DLL_DIRECTORY_HANDLE
    if _API is not None:
        return _API

    if not KINESIS_DIR.is_dir():
        raise MotorError(f"Thorlabs Kinesis not found at {KINESIS_DIR}.")

    if str(KINESIS_DIR) not in sys.path:
        sys.path.append(str(KINESIS_DIR))
    if hasattr(os, "add_dll_directory") and _DLL_DIRECTORY_HANDLE is None:
        _DLL_DIRECTORY_HANDLE = os.add_dll_directory(str(KINESIS_DIR))

    try:
        import clr  # type: ignore

        for dll_name in (
            "Thorlabs.MotionControl.DeviceManagerCLI.dll",
            "Thorlabs.MotionControl.GenericMotorCLI.dll",
            "Thorlabs.MotionControl.IntegratedStepperMotorsCLI.dll",
        ):
            dll_path = KINESIS_DIR / dll_name
            if not dll_path.is_file():
                raise MotorError(f"Missing Kinesis DLL: {dll_path}")
            clr.AddReference(str(dll_path))

        from System import Decimal
        from Thorlabs.MotionControl.DeviceManagerCLI import (  # type: ignore
            DeviceConfiguration,
            DeviceManagerCLI,
        )
        from Thorlabs.MotionControl.GenericMotorCLI.Settings import (  # type: ignore
            MotorDirection,
        )
        from Thorlabs.MotionControl.IntegratedStepperMotorsCLI import (  # type: ignore
            CageRotator,
        )
    except MotorError:
        raise
    except Exception as exc:
        raise MotorError(
            "Could not load the Kinesis API. Confirm 64-bit Python, "
            "pythonnet, and Thorlabs Kinesis are all installed."
        ) from exc

    _API = {
        "Decimal": Decimal,
        "DeviceConfiguration": DeviceConfiguration,
        "DeviceManagerCLI": DeviceManagerCLI,
        "MotorDirection": MotorDirection,
        "CageRotator": CageRotator,
    }
    return _API


def angular_error_deg(commanded: float, reported: float) -> float:
    """Shortest absolute distance between two circular (0-360) coordinates."""

    return abs((reported - commanded + 180.0) % 360.0 - 180.0)


class CageRotatorMotor:
    """One K10CR2/M rotation stage. dry_run simulates every operation
    (including continuous spin, via elapsed-wall-clock-time angle
    advancement) so the whole calling script can be exercised with no
    hardware and no Kinesis/pythonnet installed."""

    def __init__(self, serial_no: str, dry_run: bool = False) -> None:
        self.serial_no = str(serial_no)
        self.dry_run = dry_run
        self.device = None
        self.connection_state = False
        self._api = None
        self._simulated_position = 0.0
        self._simulated_velocity_deg_s = 0.0
        self._spinning = False
        self._spin_start: tuple[float, float] | None = None  # (monotonic time, angle at start)

    def connect(self) -> None:
        """Find, connect, and initialize this motor's device by serial number."""

        if self.dry_run:
            self.connection_state = True
            print(f"[dry-run] Simulated motor {self.serial_no} connected.")
            return

        self._api = _load_kinesis_api()
        manager = self._api["DeviceManagerCLI"]
        manager.BuildDeviceList()
        visible = [str(item) for item in manager.GetDeviceList()]
        if self.serial_no not in visible:
            raise MotorError(f"Motor {self.serial_no} not found; USB devices: {visible or 'none'}")

        try:
            self.device = self._api["CageRotator"].CreateCageRotator(self.serial_no)
            self.device.Connect(self.serial_no)
            if not self.device.IsSettingsInitialized():
                self.device.WaitForSettingsInitialized(10_000)
            if not self.device.IsSettingsInitialized():
                raise MotorError(f"Motor {self.serial_no} settings initialization timed out.")

            self.device.StartPolling(250)
            time.sleep(0.5)
            self.device.EnableDevice()
            time.sleep(0.5)
            self.device.LoadMotorConfiguration(
                self.serial_no,
                self._api["DeviceConfiguration"].DeviceSettingsUseOptionType.UseDeviceSettings,
            )
            self.connection_state = True
        except Exception as exc:
            self.disconnect()
            if isinstance(exc, MotorError):
                raise
            raise MotorError(f"Motor {self.serial_no} connection failed: {exc}") from exc

    def set_velocity(self, max_velocity_deg_s: float, acceleration_deg_s2: float) -> None:
        """Set the max velocity/acceleration used by every subsequent
        point-to-point move AND by start_continuous()."""

        self._require_connection()
        self._simulated_velocity_deg_s = float(max_velocity_deg_s)
        if self.dry_run:
            return
        try:
            decimal = self._api["Decimal"]
            self.device.SetVelocityParams(
                decimal(float(acceleration_deg_s2)),
                decimal(float(max_velocity_deg_s)),
            )
        except Exception as exc:
            raise MotorError(f"Motor {self.serial_no} velocity setup failed: {exc}") from exc

    def set_home_velocity(self, home_velocity_deg_s: float) -> None:
        self._require_connection()
        if self.dry_run:
            return
        try:
            params = self.device.GetHomingParams()
            params.Velocity = self._api["Decimal"](float(home_velocity_deg_s))
            self.device.SetHomingParams(params)
        except Exception as exc:
            raise MotorError(f"Motor {self.serial_no} home-velocity setup failed: {exc}") from exc

    def home_with_speed(self, home_velocity_deg_s: float, timeout_ms: int = 60_000) -> None:
        self.set_home_velocity(home_velocity_deg_s)
        if self.dry_run:
            self._simulated_position = 0.0
            return
        try:
            self.device.Home(int(timeout_ms))
        except Exception as exc:
            raise MotorError(f"Motor {self.serial_no} homing failed: {exc}") from exc

    def is_homed_high_level(self) -> bool | None:
        self._require_connection()
        if self.dry_run:
            return True
        try:
            return bool(self.device.Status.IsHomed)
        except Exception:
            return None

    def move_cage_rotator_to(
        self,
        position_deg: float,
        timeout_ms: int = 60_000,
        tolerance_deg: float = 0.1,
    ) -> float:
        """Point-to-point move to a MOTOR angle (already offset-corrected),
        verified against the encoder readback. Never call while spinning
        continuously -- stop_continuous() first."""

        self._require_connection()
        target = float(position_deg) % 360.0
        if self.dry_run:
            self._simulated_position = target
            return target
        try:
            self.device.MoveTo(self._api["Decimal"](target), int(timeout_ms))
            reported = self.read_pos()
        except Exception as exc:
            raise MotorError(f"Motor {self.serial_no} move to {target:.4f}° failed: {exc}") from exc

        error = angular_error_deg(target, reported)
        if error > float(tolerance_deg):
            raise MotorError(
                f"Motor {self.serial_no} position error too large: command={target:.4f}°, "
                f"encoder={reported:.4f}°, error={error:.4f}°."
            )
        return reported

    def start_continuous(self, forward: bool = True) -> None:
        """Begin continuous rotation (non-blocking). set_velocity() must be
        called first. Never blocks -- poll read_pos() to track progress."""

        self._require_connection()
        if self.dry_run:
            self._spinning = True
            self._spin_start = (time.monotonic(), self._simulated_position)
            return
        direction = self._api["MotorDirection"].Forward if forward else self._api["MotorDirection"].Backward
        self.device.MoveContinuous(direction)
        self._spinning = True

    def stop_continuous(self, timeout_ms: int = 60_000) -> None:
        """Stop continuous rotation (blocking until stopped)."""

        self._require_connection()
        if self.dry_run:
            if self._spinning:
                self._simulated_position = self._dry_run_spinning_angle()
            self._spinning = False
            self._spin_start = None
            return
        try:
            self.device.Stop(int(timeout_ms))
        finally:
            self._spinning = False

    def _dry_run_spinning_angle(self) -> float:
        start_time, start_angle = self._spin_start
        elapsed = time.monotonic() - start_time
        return (start_angle + self._simulated_velocity_deg_s * elapsed) % 360.0

    def read_pos(self) -> float:
        self._require_connection()
        if self.dry_run:
            return self._dry_run_spinning_angle() if self._spinning else self._simulated_position
        try:
            return float(str(self.device.Position))
        except Exception as exc:
            raise MotorError(f"Motor {self.serial_no} encoder read failed: {exc}") from exc

    def stop(self) -> None:
        """Best-effort immediate stop. Never raises."""

        self._spinning = False
        if self.dry_run or self.device is None:
            return
        try:
            self.device.StopImmediate()
        except Exception:
            try:
                self.device.Stop(3_000)
            except Exception:
                pass

    def disconnect(self) -> None:
        if self.device is not None:
            try:
                self.device.StopPolling()
            except Exception:
                pass
            try:
                self.device.Disconnect()
            except Exception:
                pass
        self.device = None
        self.connection_state = False

    def _require_connection(self) -> None:
        if not self.connection_state:
            raise MotorError(f"Motor {self.serial_no} is not connected.")

    def __enter__(self):
        self.connect()
        return self

    def __exit__(self, exc_type, exc, traceback):
        self.disconnect()

    def __del__(self):
        self.disconnect()


# Backward-compatible alias matching 20260813/K10CR2_communication_ver2.py's
# lowercase class name, in case any ad-hoc script imports it that way.
cageRotator = CageRotatorMotor
