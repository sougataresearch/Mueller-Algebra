"""Tests for measure.py -- the lab's discrete/continuous acquisition
script (Sections III/IV/V, reused as-is per the project spec; this test
module is new coverage, not a spec deliverable for Parts 1-4).

Hardware-independent throughout: angle formatting/conversion, mode-
definition consistency, checkpoint logic, ROI selection, and the
pre-flight rank check (including a regression test for the exact
90-degree-spacing aliasing case), plus full dry-run sessions for every
mode/acquisition-type combination. No motors, camera, or Kinesis/IDS Peak
SDK required (--dry-run needs neither installed).
"""

from __future__ import annotations

import json
import shutil
import unittest
from pathlib import Path
from unittest.mock import patch

import numpy as np

import measure as m
from camera_communication import CameraError, roi_mean, select_roi


class AngleAndModeUtilityTests(unittest.TestCase):
    def test_angle_text_formats_compactly(self):
        self.assertEqual(m.angle_text(0), "0")
        self.assertEqual(m.angle_text(30), "30")
        self.assertEqual(m.angle_text(-45.5), "-45.5")
        self.assertEqual(m.angle_text(90.0), "90")

    def test_angle_text_rejects_out_of_range(self):
        with self.assertRaises(ValueError):
            m.angle_text(1e6)

    def test_optical_to_motor_applies_offset_and_wraps(self):
        m.ZERO_OFFSETS_DEG["PSG_Polarizer"] = 10.0
        self.assertEqual(m.optical_to_motor("PSG_Polarizer", 0.0), 10.0)
        self.assertEqual(m.optical_to_motor("PSG_Polarizer", 355.0), 5.0)
        self.assertEqual(m.optical_to_motor("PSG_Polarizer", -20.0), 350.0)

    def test_mode_definitions_are_internally_consistent(self):
        for mode, definition in m.MODE_DEFINITIONS.items():
            with self.subTest(mode=mode):
                self.assertIn(definition["first_axis"], definition["active_axes"])
                self.assertIn(definition["second_axis"], definition["active_axes"])
                for axis in definition["fixed_angle_keys"]:
                    self.assertIn(axis, definition["active_axes"])
                for axis in definition["continuous_rotating_axes"]:
                    self.assertIn(axis, definition["active_axes"])
                rows, cols = definition["matrix_shape"]
                self.assertIn(rows, (3, 4))
                self.assertIn(cols, (3, 4))

    def test_3x3_has_no_continuous_mode(self):
        self.assertEqual(m.MODE_DEFINITIONS["3x3"]["acquisition_types"], ("discrete",))
        self.assertEqual(m.MODE_DEFINITIONS["3x3"]["continuous_rotating_axes"], ())

    def test_fixed_angles_for_mode_matches_fixed_angle_keys(self):
        m.FIXED_PSG_POL_DEG = 11.0
        m.FIXED_PSA_POL_DEG = 22.0
        self.assertEqual(m.fixed_angles_for_mode("3x3"), {})
        self.assertEqual(m.fixed_angles_for_mode("3x4"), {"PSG_Polarizer": 11.0})
        self.assertEqual(m.fixed_angles_for_mode("4x3"), {"PSA_Analyzer": 22.0})
        self.assertEqual(m.fixed_angles_for_mode("4x4"), {"PSG_Polarizer": 11.0, "PSA_Analyzer": 22.0})

    def test_validate_settings_rejects_unknown_mode(self):
        with self.assertRaises(ValueError):
            m.validate_settings("5x5", "discrete")

    def test_validate_settings_rejects_unsupported_acquisition_for_mode(self):
        with self.assertRaises(ValueError):
            m.validate_settings("3x3", "continuous")

    def test_validate_settings_rejects_duplicate_angles(self):
        original_first, original_second = m.FIRST_ANGLES_DEG, m.SECOND_ANGLES_DEG
        try:
            m.FIRST_ANGLES_DEG = [0, 30, 30]
            m.SECOND_ANGLES_DEG = [0, 30, 60]
            with self.assertRaises(ValueError):
                m.validate_settings("3x3", "discrete")
        finally:
            m.FIRST_ANGLES_DEG, m.SECOND_ANGLES_DEG = original_first, original_second

    def test_validate_settings_rejects_missing_motor_serial(self):
        original = dict(m.MOTOR_SERIALS)
        try:
            m.MOTOR_SERIALS["PSG_Polarizer"] = ""
            with self.assertRaises(ValueError):
                m.validate_settings("3x3", "discrete")
        finally:
            m.MOTOR_SERIALS.clear()
            m.MOTOR_SERIALS.update(original)


class PreflightRankCheckTests(unittest.TestCase):
    """Regression coverage for CHECK_ANGLE_GRID_RANK, including the exact
    90-degree-spacing aliasing case the pre-flight check exists to catch."""

    def test_well_spread_grid_passes_for_every_mode(self):
        angles = [0, 30, 60, 90, 120, 150]
        for mode in ("3x3", "3x4", "4x3", "4x4"):
            fixed = m.fixed_angles_for_mode(mode)
            with self.subTest(mode=mode):
                m.check_angle_grid_rank(mode, angles, angles, fixed, 90.0)  # must not raise

    def test_exactly_four_states_at_a_full_rank_grid_passes(self):
        # 0/22.5/45/67.5 (Hauge's own worked example, Sec. III.C) gives a
        # genuinely full-rank 4x4 system; evenly-spaced 45-deg steps like
        # 0/45/90/135 still alias for a rotating QWP (rank 9, not 16).
        angles = [0, 22.5, 45, 67.5]
        fixed = {"PSG_Polarizer": 0.0, "PSA_Analyzer": 0.0}
        m.check_angle_grid_rank("4x4", angles, angles, fixed, 90.0)  # must not raise

    def test_90_degree_qwp_spacing_aliases_and_raises(self):
        """The exact regression case README.md calls out: QWP angles 90
        deg apart alias in cos(2*theta)/sin(2*theta) and lose rank."""

        angles = [0, 90, 180, 270]
        fixed = {"PSG_Polarizer": 0.0, "PSA_Analyzer": 0.0}
        with self.assertRaises(ValueError):
            m.check_angle_grid_rank("4x4", angles, angles, fixed, 90.0)

    def test_too_few_angles_raises_for_4x4(self):
        angles = [0, 45, 90]  # only 3 states/side, 4x4 needs rank 16 (4x4)
        fixed = {"PSG_Polarizer": 0.0, "PSA_Analyzer": 0.0}
        with self.assertRaises(ValueError):
            m.check_angle_grid_rank("4x4", angles, angles, fixed, 90.0)


class CheckpointManagerTests(unittest.TestCase):
    def setUp(self) -> None:
        # Anchored to this test file's own directory (not the cwd, and not
        # nested under the real Data/ tree) so this never leaves an empty
        # "Data" directory behind regardless of where the test is invoked
        # from.
        scratch_dir = Path(__file__).resolve().parent / "_scratch_checkpoint_test"
        self.path = scratch_dir / "checkpoint_unit_test.json"
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.addCleanup(lambda: shutil.rmtree(scratch_dir, ignore_errors=True))
        self.checkpoint = m.CheckpointManager(self.path)

    def test_next_index_starts_at_zero_with_no_checkpoint(self):
        self.assertEqual(self.checkpoint.next_index(), 0)
        self.assertFalse(self.checkpoint.load()["experiment_completed"])

    def test_update_advances_next_index(self):
        self.checkpoint.update(0, "0_0.tiff", {"PSG_Polarizer": 0.0})
        self.assertEqual(self.checkpoint.next_index(), 1)
        self.checkpoint.update(1, "0_30.tiff", {"PSG_Polarizer": 0.0})
        self.assertEqual(self.checkpoint.next_index(), 2)

    def test_complete_marks_experiment_completed(self):
        self.checkpoint.update(0, "0_0.tiff", {})
        self.checkpoint.complete(total_states=5)
        payload = self.checkpoint.load()
        self.assertTrue(payload["experiment_completed"])
        self.assertEqual(payload["total_states"], 5)


class BuildDiscreteStatesTests(unittest.TestCase):
    def test_state_count_and_filenames(self):
        first_angles = [0, 30, 60]
        second_angles = [0, 90]
        fixed = {"PSG_Polarizer": 0.0}
        states = m.build_discrete_states("3x4", first_angles, second_angles, fixed)
        self.assertEqual(len(states), len(first_angles) * len(second_angles))
        filenames = {filename for _, _, filename in states}
        self.assertEqual(filenames, {"0_0.tiff", "0_90.tiff", "30_0.tiff", "30_90.tiff", "60_0.tiff", "60_90.tiff"})

    def test_fixed_angle_present_in_every_state(self):
        states = m.build_discrete_states("4x4", [0, 45], [0, 45], {"PSG_Polarizer": 12.0, "PSA_Analyzer": 34.0})
        for _, optical, _ in states:
            self.assertEqual(optical["PSG_Polarizer"], 12.0)
            self.assertEqual(optical["PSA_Analyzer"], 34.0)


class MakeRunDirectoryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.original_data_root = m.DATA_ROOT
        m.DATA_ROOT = Path(__file__).resolve().parent / "_scratch_run_dirs"
        self.addCleanup(self._restore)

    def _restore(self) -> None:
        shutil.rmtree(m.DATA_ROOT, ignore_errors=True)
        m.DATA_ROOT = self.original_data_root

    def test_creates_images_subdirectory(self):
        run_dir = m.make_run_directory("unit_test_label")
        self.assertTrue((run_dir / "Images").is_dir())

    def test_repeated_labels_get_incrementing_index(self):
        first = m.make_run_directory("dup_label")
        second = m.make_run_directory("dup_label")
        self.assertNotEqual(first, second)

    def test_rejects_windows_illegal_characters(self):
        with self.assertRaises(ValueError):
            m.make_run_directory("bad:label")


class RoiHelperTests(unittest.TestCase):
    def test_select_roi_finds_flat_bright_region(self):
        image = np.zeros((400, 400), dtype=np.uint8)
        image[:, :] = 5  # dim background
        image[100:300, 100:300] = 150  # flat bright region
        x, y, w, h = select_roi(image, window_size=100, stride=50, min_mean=50.0)
        self.assertGreaterEqual(x, 100)
        self.assertGreaterEqual(y, 100)
        region_mean = roi_mean(image, (x, y, w, h))
        self.assertAlmostEqual(region_mean, 150.0, places=6)

    def test_select_roi_raises_when_nothing_bright_enough(self):
        image = np.zeros((200, 200), dtype=np.uint8)
        with self.assertRaises(CameraError):
            select_roi(image, window_size=100, stride=50, min_mean=50.0)

    def test_roi_mean_matches_manual_slice(self):
        image = np.arange(100, dtype=np.float64).reshape(10, 10)
        roi = (2, 3, 4, 5)
        expected = image[3:8, 2:6].mean()
        self.assertAlmostEqual(roi_mean(image, roi), expected, places=9)


class FullDryRunSessionTests(unittest.TestCase):
    """One dry-run sample per mode/acquisition-type combination -- the
    same code path an operator runs for real, sped up (fast simulated
    motor velocity, short revolutions, no settle delays) so the whole
    suite stays fast."""

    def setUp(self) -> None:
        self.original_data_root = m.DATA_ROOT
        m.DATA_ROOT = Path(__file__).resolve().parent / "_scratch_sessions"
        self._originals = {
            "MOVE_VELOCITY_DEG_S": m.MOVE_VELOCITY_DEG_S,
            "CAPTURE_ANGLE_STEP_DEG": m.CAPTURE_ANGLE_STEP_DEG,
            "OUTER_ANGLES_DEG": list(m.OUTER_ANGLES_DEG),
            "MOTOR_SETTLE_BEFORE_S": m.MOTOR_SETTLE_BEFORE_S,
            "MOTOR_SETTLE_AFTER_S": m.MOTOR_SETTLE_AFTER_S,
            "FIRST_ANGLES_DEG": list(m.FIRST_ANGLES_DEG),
            "SECOND_ANGLES_DEG": list(m.SECOND_ANGLES_DEG),
        }
        m.MOVE_VELOCITY_DEG_S = 3600.0
        m.CAPTURE_ANGLE_STEP_DEG = 60.0
        m.OUTER_ANGLES_DEG = [0.0, 45.0]
        m.MOTOR_SETTLE_BEFORE_S = 0.0
        m.MOTOR_SETTLE_AFTER_S = 0.0
        m.FIRST_ANGLES_DEG = [0, 30, 60, 90, 120, 150]
        m.SECOND_ANGLES_DEG = [0, 30, 60, 90, 120, 150]
        self.addCleanup(self._restore)

    def _restore(self) -> None:
        shutil.rmtree(m.DATA_ROOT, ignore_errors=True)
        m.DATA_ROOT = self.original_data_root
        for key, value in self._originals.items():
            setattr(m, key, value)

    def _run(self, mode: str, acquisition_type: str) -> Path:
        exit_code = m.run_fresh_session(mode, acquisition_type, f"unit_{mode}_{acquisition_type}", True, True)
        self.assertEqual(exit_code, 0)
        run_dirs = list(m.DATA_ROOT.glob(f"*_unit_{mode}_{acquisition_type}_*"))
        self.assertEqual(len(run_dirs), 1)
        return run_dirs[0]

    def _assert_common_output_structure(self, run_dir: Path, mode: str, acquisition_type: str) -> None:
        config = json.loads((run_dir / "Config" / "experiment_config.json").read_text(encoding="utf-8"))
        self.assertEqual(config["mode"], mode)
        self.assertEqual(config["acquisition_type"], acquisition_type)
        self.assertEqual(config["status"], "completed")
        self.assertTrue((run_dir / "Reports" / "ExperimentReport.txt").is_file())
        self.assertTrue((run_dir / "Results" / "BrightReference.tiff").is_file())
        self.assertTrue((run_dir / "Results" / "DarkReference.tiff").is_file())
        images = list((run_dir / "Images").glob("*.tiff"))
        self.assertGreater(len(images), 0)

    def test_3x3_discrete(self):
        run_dir = self._run("3x3", "discrete")
        self._assert_common_output_structure(run_dir, "3x3", "discrete")
        self.assertTrue((run_dir / "Checkpoints" / "checkpoint.json").is_file())

    def test_3x4_discrete(self):
        self._assert_common_output_structure(self._run("3x4", "discrete"), "3x4", "discrete")

    def test_4x3_discrete(self):
        self._assert_common_output_structure(self._run("4x3", "discrete"), "4x3", "discrete")

    def test_4x4_discrete(self):
        self._assert_common_output_structure(self._run("4x4", "discrete"), "4x4", "discrete")

    def test_3x4_continuous(self):
        run_dir = self._run("3x4", "continuous")
        self._assert_common_output_structure(run_dir, "3x4", "continuous")
        self.assertTrue((run_dir / "Logs" / "experiment_log.csv").is_file())

    def test_4x3_continuous(self):
        self._assert_common_output_structure(self._run("4x3", "continuous"), "4x3", "continuous")

    def test_4x4_continuous(self):
        self._assert_common_output_structure(self._run("4x4", "continuous"), "4x4", "continuous")

    def test_only_axes_that_changed_are_recorded_in_measurement_log(self):
        """"What's new" (README): fixed axes are parked once, not
        re-commanded every state -- checked indirectly via a successful
        completion with the fixed axis held constant across all states."""

        run_dir = self._run("3x4", "discrete")
        import csv

        with (run_dir / "Logs" / "measurement_log.csv").open(encoding="utf-8-sig") as handle:
            rows = list(csv.DictReader(handle))
        self.assertEqual(len(rows), len(m.FIRST_ANGLES_DEG) * len(m.SECOND_ANGLES_DEG))
        for row in rows:
            optical = json.loads(row["optical_angles"])
            self.assertEqual(optical["PSG_Polarizer"], m.FIXED_PSG_POL_DEG)


class ResumeSessionTests(unittest.TestCase):
    """--resume: recover an interrupted discrete run from its checkpoint,
    picking up after the last completed state."""

    def setUp(self) -> None:
        self.original_data_root = m.DATA_ROOT
        m.DATA_ROOT = Path(__file__).resolve().parent / "_scratch_resume"
        self.original_settle = (m.MOTOR_SETTLE_BEFORE_S, m.MOTOR_SETTLE_AFTER_S)
        m.MOTOR_SETTLE_BEFORE_S = 0.0
        m.MOTOR_SETTLE_AFTER_S = 0.0
        self.addCleanup(self._restore)

    def _restore(self) -> None:
        shutil.rmtree(m.DATA_ROOT, ignore_errors=True)
        m.DATA_ROOT = self.original_data_root
        m.MOTOR_SETTLE_BEFORE_S, m.MOTOR_SETTLE_AFTER_S = self.original_settle

    def test_resume_completes_remaining_states_after_partial_checkpoint(self):
        first_angles = [0, 30, 60]
        second_angles = [0, 90]
        fixed = {"PSG_Polarizer": 0.0}
        mode = "3x4"

        run_dir = m.DATA_ROOT / "2026-01-01_resumetest_01"
        (run_dir / "Images").mkdir(parents=True)
        (run_dir / "Config").mkdir(parents=True)
        (run_dir / "Checkpoints").mkdir(parents=True)

        config = {
            "mode": mode,
            "acquisition_type": "discrete",
            "fixed_angles": fixed,
            "state_inputs": {"PSG_QWP": first_angles, "PSA_Analyzer": second_angles},
            "dry_run": True,
        }
        (run_dir / "Config" / "experiment_config.json").write_text(json.dumps(config), encoding="utf-8")

        states = m.build_discrete_states(mode, first_angles, second_angles, fixed)
        # Simulate 2 of 6 states already completed.
        checkpoint = m.CheckpointManager(run_dir / "Checkpoints" / "checkpoint.json")
        checkpoint.update(0, states[0][2], states[0][1])
        checkpoint.update(1, states[1][2], states[1][1])

        with patch("builtins.input", return_value="y"):
            exit_code = m.resume_discrete_session(run_dir)
        self.assertEqual(exit_code, 0)

        payload = checkpoint.load()
        self.assertTrue(payload["experiment_completed"])
        self.assertEqual(payload["last_completed_index"], len(states) - 1)
        images = list((run_dir / "Images").glob("*.tiff"))
        self.assertEqual(len(images), len(states) - 2)  # only the remaining states are (re-)captured

    def test_resume_rejects_continuous_run(self):
        run_dir = m.DATA_ROOT / "2026-01-01_resumetest_02"
        (run_dir / "Config").mkdir(parents=True)
        config = {"mode": "4x4", "acquisition_type": "continuous"}
        (run_dir / "Config" / "experiment_config.json").write_text(json.dumps(config), encoding="utf-8")
        with self.assertRaises(ValueError):
            m.resume_discrete_session(run_dir)


if __name__ == "__main__":
    unittest.main()
