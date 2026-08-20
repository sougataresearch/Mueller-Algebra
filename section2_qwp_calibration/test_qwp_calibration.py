"""Tests for qwp_calibration.py's Part 1 additions: the Hauge Eq. 19-21/
Eq. 4 closed-form math (already correct in the original draft), the
golden-section null search, and a full dry-run session exercising the
DryRunOpticalBench's hidden ground truth end to end. No hardware, no
motors, no camera required (--dry-run needs neither Kinesis/pythonnet nor
the IDS Peak SDK installed).
"""

from __future__ import annotations

import shutil
import sys
import unittest
from pathlib import Path

import numpy as np

# motor_communication.py lives in ../common/, shared by every section.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "common"))

import qwp_calibration as qc


class ComputeCalibrationMathTests(unittest.TestCase):
    """Regression coverage for the original draft's closed-form math
    (Hauge Eq. 19-21, Eq. 4) -- unchanged by Part 1, still correct."""

    def test_recovers_known_defects_from_synthetic_r1_r2_r3(self):
        s_true, f_true = 0.03, 0.47
        p_true = 1.0 - 2.0 * f_true
        r_true = np.sqrt(1.0 - p_true**2 - s_true**2)

        # Hauge Eq. (19): R1=(1+s)gIp, R2=(1-f)gIp, R3=(1-s)gIp.
        g_ip = 100.0
        frames = {
            0.0: np.full((4, 4), (1.0 + s_true) * g_ip),
            45.0: np.full((4, 4), (1.0 - f_true) * g_ip),
            90.0: np.full((4, 4), (1.0 - s_true) * g_ip),
        }
        maps = qc.compute_calibration(frames)
        self.assertTrue(np.allclose(maps["s"], s_true, atol=1e-9))
        self.assertTrue(np.allclose(maps["f"], f_true, atol=1e-9))
        self.assertTrue(np.allclose(maps["r"], r_true, atol=1e-9))
        expected_delta = np.degrees(np.arctan2(r_true, p_true))
        self.assertTrue(np.allclose(maps["delta_deg"], expected_delta, atol=1e-6))

    def test_ideal_qwp_gives_90_degree_retardance(self):
        g_ip = 50.0
        frames = {0.0: np.full((2, 2), g_ip), 45.0: np.full((2, 2), 0.5 * g_ip), 90.0: np.full((2, 2), g_ip)}
        maps = qc.compute_calibration(frames)
        self.assertTrue(np.allclose(maps["delta_deg"], 90.0, atol=1e-6))
        self.assertTrue(np.allclose(maps["T"], 1.0, atol=1e-6))

    def test_unphysical_pixels_are_clipped_and_counted(self):
        # s, f chosen so p^2+s^2 > 1 (radicand negative) at some pixels.
        frames = {
            0.0: np.array([[100.0, 10.0]]),
            45.0: np.array([[0.0, 5.0]]),
            90.0: np.array([[0.0, 10.0]]),
        }
        maps = qc.compute_calibration(frames)
        self.assertGreaterEqual(maps["n_unphysical_pixels"], 1)
        self.assertTrue(np.all(maps["r"][np.isnan(maps["r"]) == False] >= 0.0))


def _synthetic_r(angle_deg: float, s_true: float, f_true: float, g_ip: float, shape=(4, 4)) -> np.ndarray:
    """R(C) at P=A=0, no sample -- Hauge Eq. (8) collapsed with A=0, the
    same forward model qwp_calibration.DryRunOpticalBench.calibration_reading
    implements. Reproduces Eq. (19)'s R1,R2,R3 exactly at C=0/45/90."""

    c = np.deg2rad(angle_deg)
    s0 = 1.0 + s_true * np.cos(2 * c)
    s1 = f_true * np.cos(4 * c) + s_true * np.cos(2 * c) + (1.0 - f_true)
    return np.full(shape, g_ip * 0.5 * (s0 + s1))


class LeastSquaresCalibrationMathTests(unittest.TestCase):
    """fit_calibration_least_squares -- the N-angle (N>3) generalization
    of Hauge Eq. (19)-(22), added on top of the unchanged 3-point solve
    above. Primary correctness gate: synthetic round-trip against known
    s,f,gIp."""

    def test_recovers_known_defects_at_minimum_three_angles(self):
        """At N=3 the fit uses the reduced (B2=B4=0 assumed) 3-parameter
        model -- fitting all 5 would be an underdetermined system, so the
        B2/B4 diagnostic simply isn't available below N=5 (see
        test_b2_b4_diagnostic_unavailable_below_five_angles)."""

        s_true, f_true, g_ip_true = 0.03, 0.47, 100.0
        angles = qc.least_squares_calibration_angles(3)
        frames = {a: _synthetic_r(a, s_true, f_true, g_ip_true) for a in angles}
        maps = qc.fit_calibration_least_squares(frames)
        self.assertTrue(np.allclose(maps["s"], s_true, atol=1e-9))
        self.assertTrue(np.allclose(maps["f"], f_true, atol=1e-9))
        self.assertTrue(np.allclose(maps["gIp"], g_ip_true, atol=1e-6))
        self.assertIsNone(maps["diagnostics"]["B2"])
        self.assertIsNone(maps["diagnostics"]["B4"])

    def test_b2_b4_diagnostic_unavailable_below_five_angles(self):
        for num_angles in (3, 4):
            angles = qc.least_squares_calibration_angles(num_angles)
            frames = {a: _synthetic_r(a, 0.01, 0.49, 100.0) for a in angles}
            maps = qc.fit_calibration_least_squares(frames)
            self.assertIsNone(maps["diagnostics"]["B2"])

    def test_b2_b4_diagnostic_near_zero_with_five_or_more_angles_when_aligned(self):
        s_true, f_true, g_ip_true = 0.015, 0.485, 100.0
        angles = qc.least_squares_calibration_angles(8)
        frames = {a: _synthetic_r(a, s_true, f_true, g_ip_true) for a in angles}
        maps = qc.fit_calibration_least_squares(frames)
        self.assertTrue(np.allclose(maps["diagnostics"]["B2"], 0.0, atol=1e-9))
        self.assertTrue(np.allclose(maps["diagnostics"]["B4"], 0.0, atol=1e-9))

    def test_matches_three_angle_closed_form_exactly_at_n_equals_3_on_hauge_angles(self):
        """least_squares_calibration_angles(3) doesn't land on 0/45/90
        (it spans 0-180 evenly, i.e. 0/60/120) -- so cross-check instead
        against a direct fit at the literal Hauge angles, which must give
        the identical answer to compute_calibration's exact solve."""

        s_true, f_true, g_ip_true = -0.02, 0.49, 80.0
        frames = {a: _synthetic_r(a, s_true, f_true, g_ip_true) for a in (0.0, 45.0, 90.0)}
        exact = qc.compute_calibration(frames)
        fitted = qc.fit_calibration_least_squares(frames)
        self.assertTrue(np.allclose(exact["s"], fitted["s"], atol=1e-9))
        self.assertTrue(np.allclose(exact["f"], fitted["f"], atol=1e-9))
        self.assertTrue(np.allclose(exact["delta_deg"], fitted["delta_deg"], atol=1e-6))

    def test_recovers_known_defects_with_many_angles(self):
        s_true, f_true, g_ip_true = 0.018, 0.492, 150.0
        angles = qc.least_squares_calibration_angles(12)
        frames = {a: _synthetic_r(a, s_true, f_true, g_ip_true) for a in angles}
        maps = qc.fit_calibration_least_squares(frames)
        self.assertTrue(np.allclose(maps["s"], s_true, atol=1e-9))
        self.assertTrue(np.allclose(maps["f"], f_true, atol=1e-9))

    def test_more_angles_reduce_noise(self):
        """The whole point of this mode: averaging over more angles should
        lower the standard deviation of the recovered s,f under noise."""

        rng = np.random.default_rng(0)
        s_true, f_true, g_ip_true = 0.02, 0.48, 100.0
        noise_sigma = 2.0

        def recovered_s_std(num_angles: int, n_trials: int = 200) -> float:
            angles = qc.least_squares_calibration_angles(num_angles)
            recovered = []
            for _ in range(n_trials):
                frames = {
                    a: _synthetic_r(a, s_true, f_true, g_ip_true, shape=(1, 1)) + rng.normal(0, noise_sigma, (1, 1))
                    for a in angles
                }
                recovered.append(float(qc.fit_calibration_least_squares(frames)["s"][0, 0]))
            return float(np.std(recovered))

        std_few = recovered_s_std(3)
        std_many = recovered_s_std(12)
        self.assertLess(std_many, std_few)

    def test_rejects_fewer_than_three_angles(self):
        frames = {0.0: np.ones((2, 2)), 90.0: np.ones((2, 2))}
        with self.assertRaises(ValueError):
            qc.fit_calibration_least_squares(frames)

    def test_least_squares_calibration_angles_spans_one_period_evenly(self):
        angles = qc.least_squares_calibration_angles(4)
        self.assertEqual(angles, (0.0, 45.0, 90.0, 135.0))

    def test_n_equals_3_uses_hauge_angles_not_60_degree_spacing(self):
        """Regression test: naively evenly-spacing N=3 across 0-180 gives
        60 deg steps (0/60/120), which aliases cos(2*60)=cos(4*60) and
        cos(2*120)=cos(4*120) together -- a singular reduced-model design
        matrix (rank 2, not 3). least_squares_calibration_angles(3) must
        return Hauge's own 0/45/90 instead."""

        self.assertEqual(qc.least_squares_calibration_angles(3), (0.0, 45.0, 90.0))

    def test_evenly_spaced_grids_are_full_rank_for_n_four_through_fifteen(self):
        for num_angles in range(4, 16):
            angles = qc.least_squares_calibration_angles(num_angles)
            theta = np.deg2rad(np.array(angles))
            design = np.stack([np.ones_like(theta), np.cos(2 * theta), np.cos(4 * theta)], axis=1)
            self.assertEqual(np.linalg.matrix_rank(design), 3, msg=f"N={num_angles}")


class GoldenSectionSearchTests(unittest.TestCase):
    def test_finds_known_minimum(self):
        minimum_at = 37.5

        def f(angle_deg):
            return (angle_deg - minimum_at) ** 2

        result = qc._golden_section_minimize(f, minimum_at - 5, minimum_at + 5, 1e-4)
        self.assertAlmostEqual(result, minimum_at, places=3)

    def test_finds_minimum_off_center(self):
        minimum_at = -2.3

        def f(angle_deg):
            return (angle_deg - minimum_at) ** 2 + 3.0

        result = qc._golden_section_minimize(f, -10, 10, 1e-4)
        self.assertAlmostEqual(result, minimum_at, places=3)


class NullIntensityMismatchWarningTests(unittest.TestCase):
    """null_intensity_mismatch_warning() -- an engineering sanity check,
    not a Hauge formula (decisions.md ADR-012): a compensator null much
    brighter than the bare crossed-P/A null flags a real problem, not
    noise. The full dry-run session never exercises the WARNING-firing
    path (DryRunOpticalBench's two nulls always land within a few counts
    of each other), so this is tested directly against both branches."""

    def test_no_warning_when_nulls_are_close(self):
        warning = qc.null_intensity_mismatch_warning(8.0, 8.2, "PSG_QWP")
        self.assertIsNone(warning)

    def test_warns_when_ratio_and_margin_both_exceeded(self):
        warning = qc.null_intensity_mismatch_warning(8.0, 20.0, "PSG_QWP")
        self.assertIsNotNone(warning)
        self.assertIn("PSG_QWP", warning)

    def test_no_warning_when_ratio_high_but_absolute_margin_not_crossed(self):
        # Both nulls sit right at the camera's dark-count floor -- a large
        # RATIO here is just noise, not a real problem, which is exactly
        # why the check requires both conditions, not the ratio alone.
        warning = qc.null_intensity_mismatch_warning(1.0, 3.0, "PSG_QWP")
        self.assertIsNone(warning)

    def test_no_warning_when_margin_high_but_ratio_not_exceeded(self):
        # A large absolute gap that still keeps the ratio under 1.5x
        # (e.g. both nulls already fairly bright) shouldn't fire either --
        # both conditions must trip, not just one.
        warning = qc.null_intensity_mismatch_warning(100.0, 110.0, "PSG_QWP")
        self.assertIsNone(warning)

    def test_handles_zero_reference_intensity_without_crashing(self):
        warning = qc.null_intensity_mismatch_warning(0.0, 6.0, "PSG_QWP")
        self.assertIsNotNone(warning)


class DryRunOpticalBenchTests(unittest.TestCase):
    """The bench's own physics model (Malus's law for the A-vs-P null,
    Hauge Eq. 8/11 collapsed for the compensator null) -- verifies the
    dry-run search has a genuine, non-trivial minimum to converge on."""

    def setUp(self) -> None:
        from motor_communication import CageRotatorMotor

        self.motors = {}
        for axis in ("PSG_Polarizer", "PSA_Analyzer", "PSG_QWP", "PSA_QWP"):
            motor = CageRotatorMotor(qc.MOTOR_SERIALS[axis], dry_run=True)
            motor.connect()
            self.motors[axis] = motor
        self.bench = qc.DryRunOpticalBench(self.motors)

    def test_crossed_polarizer_minimum_at_true_90(self):
        true_zero_offset = qc.ZERO_OFFSETS_DEG["PSA_Analyzer"] + self.bench.HIDDEN_OFFSET_ERROR_DEG["PSA_Analyzer"]
        motor_angle_at_true_90 = (true_zero_offset + 90.0) % 360.0
        self.motors["PSA_Analyzer"].move_cage_rotator_to(motor_angle_at_true_90, timeout_ms=1000, tolerance_deg=0.1)
        minimum_intensity = self.bench.crossed_polarizer_intensity()

        self.motors["PSA_Analyzer"].move_cage_rotator_to(
            (motor_angle_at_true_90 + 30.0) % 360.0, timeout_ms=1000, tolerance_deg=0.1
        )
        off_minimum_intensity = self.bench.crossed_polarizer_intensity()
        self.assertLess(minimum_intensity, off_minimum_intensity)
        self.assertAlmostEqual(minimum_intensity, self.bench.NOISE_FLOOR, places=6)

    def test_compensator_null_minimum_at_true_0(self):
        target = "PSG_QWP"
        true_zero_offset = qc.ZERO_OFFSETS_DEG[target] + self.bench.HIDDEN_OFFSET_ERROR_DEG[target]
        self.motors[target].move_cage_rotator_to(true_zero_offset % 360.0, timeout_ms=1000, tolerance_deg=0.1)
        minimum_intensity = self.bench.compensator_null_intensity(target)

        self.motors[target].move_cage_rotator_to((true_zero_offset + 45.0) % 360.0, timeout_ms=1000, tolerance_deg=0.1)
        off_minimum_intensity = self.bench.compensator_null_intensity(target)
        self.assertLess(minimum_intensity, off_minimum_intensity)
        self.assertAlmostEqual(minimum_intensity, self.bench.NOISE_FLOOR, places=6)

    def test_calibration_reading_matches_hauge_r1_r2_r3_ratios(self):
        target = "PSG_QWP"
        true_zero_offset = qc.ZERO_OFFSETS_DEG[target] + self.bench.HIDDEN_OFFSET_ERROR_DEG[target]
        readings = {}
        for angle in (0.0, 45.0, 90.0):
            self.motors[target].move_cage_rotator_to((true_zero_offset + angle) % 360.0, timeout_ms=1000, tolerance_deg=0.1)
            readings[angle] = self.bench.calibration_reading(target)
        # Dark-subtract the bench's NOISE_FLOOR first, exactly as the real
        # capture pipeline does (DARK_SUBTRACT) -- Hauge's R1/R2/R3 ratios
        # assume readings proportional to gIp with no additive offset.
        floor = self.bench.NOISE_FLOOR
        r0, r45, r90 = readings[0.0] - floor, readings[45.0] - floor, readings[90.0] - floor
        s_recovered = (r0 - r90) / (r0 + r90)
        f_recovered = (r0 - 2 * r45 + r90) / (r0 + r90)
        defects = self.bench.HIDDEN_DEFECTS[target]
        self.assertAlmostEqual(s_recovered, defects["s"], places=4)
        self.assertAlmostEqual(f_recovered, defects["f"], places=4)


class FullDryRunSessionTests(unittest.TestCase):
    """End-to-end: automated null search for both QWPs, back-to-back, in
    one dry-run session -- exercises the same code path an operator would
    run for real, with --dry-run --no-prompt."""

    def setUp(self) -> None:
        # Anchored to this test file's own directory (not the cwd, and not
        # nested under the real Data/ tree) so this never leaves an empty
        # "Data" directory behind regardless of where the test is invoked
        # from.
        self.output_root = Path(__file__).resolve().parent / "_scratch_qwp_calibration"
        qc.OUTPUT_ROOT = self.output_root
        self.addCleanup(lambda: shutil.rmtree(self.output_root, ignore_errors=True))

    def test_both_qwps_calibrated_within_tolerance(self):
        exit_code = qc.run_calibration(("PSG_QWP", "PSA_QWP"), "automated", dry_run=True, no_prompt=True)
        self.assertEqual(exit_code, 0)

        run_dirs = list(self.output_root.glob("*"))
        self.assertEqual(len(run_dirs), 1)
        import json

        report = json.loads((run_dirs[0] / "Config" / "calibration_result.json").read_text())
        for target in ("PSG_QWP", "PSA_QWP"):
            summary = report["results"][target]["summary"]
            self.assertGreater(summary["delta_deg"]["median"], 80.0)
            self.assertLess(summary["delta_deg"]["median"], 100.0)
            self.assertLess(abs(summary["s"]["median"]), 0.15)
            self.assertLess(abs(summary["f"]["median"] - 0.5), 0.15)

        discovered = json.loads((run_dirs[0] / "Config" / "discovered_zero_offsets.json").read_text())
        self.assertIn("PSG_QWP", discovered["discovered"])
        self.assertIn("PSA_QWP", discovered["discovered"])
        self.assertIn("PSA_Analyzer", discovered["discovered"])

    def test_least_squares_mode_calibrated_within_tolerance(self):
        exit_code = qc.run_calibration(
            ("PSG_QWP",), "automated", dry_run=True, no_prompt=True,
            angle_mode="least_squares", num_least_squares_angles=8,
        )
        self.assertEqual(exit_code, 0)

        run_dirs = list(self.output_root.glob("*"))
        self.assertEqual(len(run_dirs), 1)
        import json

        report = json.loads((run_dirs[0] / "Config" / "calibration_result.json").read_text())
        entry = report["results"]["PSG_QWP"]
        self.assertEqual(entry["angle_mode"], "least_squares")
        self.assertEqual(len(entry["calibration_angles_deg"]), 8)
        summary = entry["summary"]
        self.assertGreater(summary["delta_deg"]["median"], 80.0)
        self.assertLess(summary["delta_deg"]["median"], 100.0)
        self.assertIn("least_squares_diagnostics", entry)
        self.assertEqual(entry["least_squares_diagnostics"]["num_angles"], 8)

        # B2/B4 diagnostic maps are saved alongside the usual s/f/p/r/delta/T ones.
        self.assertTrue((run_dirs[0] / "Results" / "PSG_QWP" / "B2_map.npy").is_file())
        self.assertTrue((run_dirs[0] / "Results" / "PSG_QWP" / "B4_map.npy").is_file())

    def test_run_acquisition_writes_images_and_experiment_config_even_in_dry_run(self):
        """run_acquisition() (MMIE_ATOMIC_TARGETS.md target 2.8 -- the
        capture/reconstruct split) must write real TIFFs to disk even in
        --dry-run, not just in real hardware mode -- the change
        _capture_or_simulate exists for, needed so
        qwp_calibration_reconstruction.py can read a dry-run session back
        from a folder the same way it would a real one."""

        run_dir = qc.run_acquisition(("PSG_QWP",), "automated", dry_run=True, no_prompt=True)
        self.assertIsNotNone(run_dir)

        self.assertTrue((run_dir / "Images" / "bright_reference.tiff").is_file())
        self.assertTrue((run_dir / "Images" / "PSG_QWP" / "dark.tiff").is_file())
        for angle in qc.CALIBRATION_ANGLES_DEG:
            self.assertTrue((run_dir / "Images" / "PSG_QWP" / f"C_{angle:g}.tiff").is_file())

        import json

        config = json.loads((run_dir / "Config" / "experiment_config.json").read_text())
        self.assertEqual(config["targets"], ["PSG_QWP"])
        self.assertEqual(config["angle_mode"], "three_angle")
        self.assertTrue(config["dry_run"])
        self.assertIn("PSG_QWP", config["null_search"])
        self.assertIn("pa_null_intensity", config["null_search"]["PSG_QWP"])
        self.assertIn("compensator_null_intensity", config["null_search"]["PSG_QWP"])
        self.assertIn("PSG_QWP", config["discovered_zero_offsets_deg"])

        # No calibration_result.json yet -- that's run_reconstruction's job, not run_acquisition's.
        self.assertFalse((run_dir / "Config" / "calibration_result.json").is_file())

    def test_split_acquisition_then_reconstruction_matches_combined_run_calibration(self):
        """The refactor's whole point: run_acquisition() + run_reconstruction()
        run separately must produce the same numbers run_calibration()
        (still exactly this two-step sequence internally) always has --
        DryRunOpticalBench has no randomness, so this should match closely,
        not just approximately."""

        split_run_dir = qc.run_acquisition(("PSG_QWP",), "automated", dry_run=True, no_prompt=True)
        split_exit_code = qc.run_reconstruction(split_run_dir)
        self.assertEqual(split_exit_code, 0)

        combined_exit_code = qc.run_calibration(("PSG_QWP",), "automated", dry_run=True, no_prompt=True)
        self.assertEqual(combined_exit_code, 0)

        run_dirs = sorted(self.output_root.glob("*"))
        self.assertEqual(len(run_dirs), 2)

        import json

        split_report = json.loads((run_dirs[0] / "Config" / "calibration_result.json").read_text())
        combined_report = json.loads((run_dirs[1] / "Config" / "calibration_result.json").read_text())
        split_summary = split_report["results"]["PSG_QWP"]["summary"]
        combined_summary = combined_report["results"]["PSG_QWP"]["summary"]
        for key in ("s", "f", "p", "r", "delta_deg", "T"):
            self.assertAlmostEqual(split_summary[key]["median"], combined_summary[key]["median"], places=6)


if __name__ == "__main__":
    unittest.main()
