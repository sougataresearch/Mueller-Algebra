"""Synthetic round-trip tests for continuous_single_arm_reconstruction.py
and continuous_single_arm_calibration.py (Section IV: 3x4/4x3 continuous
modes). No hardware, no motors, no camera required.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

import numpy as np

import continuous_single_arm_calibration as cal
import continuous_single_arm_reconstruction as csa

# This test borrows Part 2's own generator/analyzer vector formulas
# (section3_discrete_reconstruction/) to build its synthetic ground-truth
# forward model -- Section IV's physics is explicitly built on the same
# Eq. 8/11 vectors, so reusing the real implementation here (rather than
# re-typing the formulas a second time) is a feature, not test coupling:
# it ties this round-trip test to the one formula set both sections share.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "section3_discrete_reconstruction"))
import discrete_reconstruction as dr


def _random_mueller(rng: np.random.Generator) -> np.ndarray:
    m = np.eye(4) + 0.15 * rng.standard_normal((4, 4))
    m[0, 0] = 1.0
    return m


def _rotating_and_outer_vectors(mode, s, f, r):
    """Both mode's rotating side (a real QWP) and outer side (a plain
    polarizer, no QWP in either mode) vector functions, matching the fixed
    companion axis at optical 0 (this method's own requirement)."""

    table = csa.MODE_TABLE[mode]
    if table["rotating_role"] == "generator":
        return (lambda c: dr.generator_vector_qwp(c, 0.0, s, f, r)), (lambda a: dr.analyzer_vector_polarizer(a)[:3])
    return (lambda c: dr.analyzer_vector_qwp(c, 0.0, s, f, r)), (lambda a: dr.generator_vector_polarizer(a)[:3])


def _intensity(mode, m_true, rot_vec, outer_vec, rotating_angle, outer_angle):
    table = csa.MODE_TABLE[mode]
    v_rot = rot_vec(rotating_angle)
    v_outer = outer_vec(outer_angle)
    if table["rotating_role"] == "generator":
        return float(v_outer @ m_true[:3, :] @ v_rot)
    return float(v_rot @ m_true[:, :3] @ v_outer)


class SingleArmReconstructionRoundTripTests(unittest.TestCase):
    def setUp(self) -> None:
        self.rng = np.random.default_rng(1)
        self.s_rot, self.f_rot = 0.02, 0.485
        self.r_rot = np.sqrt(1 - (1 - 2 * self.f_rot) ** 2 - self.s_rot**2)
        self.rotating_cal = {"s": self.s_rot, "f": self.f_rot, "r": self.r_rot}

    def _check(self, mode, n_outer=7, n_frames=180, noise=0.0):
        m_true = _random_mueller(self.rng)
        rot_vec, outer_vec = _rotating_and_outer_vectors(mode, self.s_rot, self.f_rot, self.r_rot)
        outer_angles = list(np.linspace(0, 150, n_outer))

        per_outer_angles, per_outer_intensity = [], []
        for outer_angle in outer_angles:
            frame_angles = np.sort(self.rng.uniform(0, 360, n_frames))
            intens = np.array([_intensity(mode, m_true, rot_vec, outer_vec, c, outer_angle) for c in frame_angles])
            if noise:
                intens = intens + noise * self.rng.standard_normal(n_frames)
            per_outer_angles.append(frame_angles)
            per_outer_intensity.append(intens)

        m_rec = csa.reconstruct_single_arm(mode, outer_angles, per_outer_angles, per_outer_intensity, self.rotating_cal, None)
        m_norm, _, _ = dr.normalize_mueller_matrix(m_rec)
        m_true_norm = m_true / m_true[0, 0]
        err = np.nanmax(np.abs(m_norm - m_true_norm))
        tol = 1e-6 if not noise else 0.5
        self.assertLess(err, tol, f"{mode} noise={noise}")
        return m_norm

    def test_4x3_exact_recovery(self):
        self._check("4x3")

    def test_3x4_exact_recovery(self):
        self._check("3x4")

    def test_4x3_graceful_noise_degradation(self):
        self._check("4x3", noise=0.02)

    def test_3x4_graceful_noise_degradation(self):
        self._check("3x4", noise=0.02)

    def test_4x3_recovers_everything_except_rightmost_column(self):
        m_norm = self._check("4x3")
        self.assertTrue(np.all(np.isnan(m_norm[:, 3])))
        self.assertFalse(np.any(np.isnan(m_norm[:, :3])))

    def test_3x4_recovers_everything_except_bottom_row(self):
        m_norm = self._check("3x4")
        self.assertTrue(np.all(np.isnan(m_norm[3, :])))
        self.assertFalse(np.any(np.isnan(m_norm[:3, :])))

    def test_measure_py_default_three_outer_angles_regression(self):
        """Regression test: measure.py's own OUTER_ANGLES_DEG default is
        exactly 3 angles ([0.0, 45.0, 90.0]). Fitting the FULL 5-parameter
        outer-angle model with only 3 angles is an underdetermined system
        that numpy.linalg.lstsq does not detect -- it silently returns a
        wrong (minimum-norm) answer instead of raising. The fix uses the
        analytically-correct REDUCED 3-parameter model whenever the outer
        side has no QWP (outer_cal=None, true for both 3x4 and 4x3), which
        needs only 3 outer angles and is not an approximation."""

        for mode in ("3x4", "4x3"):
            m_true = _random_mueller(self.rng)
            rot_vec, outer_vec = _rotating_and_outer_vectors(mode, self.s_rot, self.f_rot, self.r_rot)
            outer_angles = [0.0, 45.0, 90.0]  # measure.py's literal OUTER_ANGLES_DEG default

            per_outer_angles, per_outer_intensity = [], []
            for outer_angle in outer_angles:
                frame_angles = np.sort(self.rng.uniform(0, 360, 180))
                intens = np.array(
                    [_intensity(mode, m_true, rot_vec, outer_vec, c, outer_angle) for c in frame_angles]
                )
                per_outer_angles.append(frame_angles)
                per_outer_intensity.append(intens)

            m_rec = csa.reconstruct_single_arm(
                mode, outer_angles, per_outer_angles, per_outer_intensity, self.rotating_cal, None
            )
            m_norm, _, _ = dr.normalize_mueller_matrix(m_rec)
            m_true_norm = m_true / m_true[0, 0]
            err = np.nanmax(np.abs(m_norm - m_true_norm))
            self.assertLess(err, 1e-6, f"{mode} with measure.py's default 3 outer angles")

    def test_more_outer_angles_reduce_noise(self):
        """The point of this fix: the reduced model works correctly at
        the minimum (3 outer angles), and averaging over MORE outer
        angles should further lower the noise on the recovered M, the
        same way it does for the rotating-side Fourier fit."""

        mode = "4x3"
        rot_vec, outer_vec = _rotating_and_outer_vectors(mode, self.s_rot, self.f_rot, self.r_rot)
        m_true = _random_mueller(self.rng)
        noise = 0.05

        def recovered_error(n_outer: int, n_frames: int = 60) -> float:
            outer_angles = list(np.linspace(0, 150, n_outer))
            per_outer_angles, per_outer_intensity = [], []
            for outer_angle in outer_angles:
                frame_angles = np.sort(self.rng.uniform(0, 360, n_frames))
                intens = np.array(
                    [_intensity(mode, m_true, rot_vec, outer_vec, c, outer_angle) for c in frame_angles]
                )
                intens = intens + noise * self.rng.standard_normal(n_frames)
                per_outer_angles.append(frame_angles)
                per_outer_intensity.append(intens)
            m_rec = csa.reconstruct_single_arm(
                mode, outer_angles, per_outer_angles, per_outer_intensity, self.rotating_cal, None
            )
            m_norm, _, _ = dr.normalize_mueller_matrix(m_rec)
            m_true_norm = m_true / m_true[0, 0]
            return float(np.nanmean(np.abs(m_norm - m_true_norm)))

        errors_few = [recovered_error(3) for _ in range(15)]
        errors_many = [recovered_error(10) for _ in range(15)]
        self.assertLess(np.mean(errors_many), np.mean(errors_few))


class FitOuterFourierTests(unittest.TestCase):
    """Direct unit coverage for the underdetermined-system guard added
    alongside the reduced-model fix."""

    def test_rejects_underdetermined_five_parameter_fit(self):
        with self.assertRaises(ValueError):
            csa.fit_outer_fourier([0.0, 45.0, 90.0], np.zeros((3, 4)), num_harmonics=5)

    def test_accepts_three_angles_for_reduced_model(self):
        stacked_v = np.zeros((3, 4))
        e_mat = csa.fit_outer_fourier([0.0, 45.0, 90.0], stacked_v, num_harmonics=3)
        self.assertEqual(e_mat.shape, (4, 5))
        self.assertTrue(np.allclose(e_mat[:, 3:], 0.0))  # cos4A/sin4A columns exactly zero, not fitted

    def test_rejects_invalid_num_harmonics(self):
        with self.assertRaises(ValueError):
            csa.fit_outer_fourier([0.0, 45.0, 90.0], np.zeros((3, 4)), num_harmonics=4)


class SingleArmCalibrationRoundTripTests(unittest.TestCase):
    def setUp(self) -> None:
        self.rng = np.random.default_rng(2)
        self.s_rot, self.f_rot = 0.018, 0.492
        self.r_rot = np.sqrt(1 - (1 - 2 * self.f_rot) ** 2 - self.s_rot**2)

    def _check(self, mode, injected_offset_deg, n_outer=6, n_frames=200):
        rot_vec, outer_vec = _rotating_and_outer_vectors(mode, self.s_rot, self.f_rot, self.r_rot)
        identity = np.eye(4)

        def intensity(true_c, outer_angle):
            return _intensity(mode, identity, rot_vec, outer_vec, true_c, outer_angle)

        # Hauge's convention: raw (logged) angle = true angle - C1', i.e.
        # true = raw + C1' -- see continuous_single_arm_calibration's
        # correct_revolution_angles docstring.
        raw_phase_angles = np.sort(self.rng.uniform(0, 360, n_frames))
        true_phase_angles = raw_phase_angles + injected_offset_deg
        phase_intensities = np.array([intensity(c, 0.0) for c in true_phase_angles])

        outer_angles = list(np.linspace(0, 150, n_outer))
        per_outer_raw, per_outer_intensity = [], []
        for outer_angle in outer_angles:
            raw = np.sort(self.rng.uniform(0, 360, n_frames))
            true_c = raw + injected_offset_deg
            per_outer_raw.append(raw)
            per_outer_intensity.append(np.array([intensity(c, outer_angle) for c in true_c]))

        result = cal.run_single_arm_calibration(
            mode, raw_phase_angles, phase_intensities, outer_angles, per_outer_raw, per_outer_intensity
        )
        self.assertAlmostEqual(result["c1_prime_deg"], injected_offset_deg, places=5)
        self.assertAlmostEqual(float(result["rotating_side"]["s"]), self.s_rot, places=6)
        self.assertAlmostEqual(float(result["rotating_side"]["f"]), self.f_rot, places=6)
        self.assertLess(result["outer_side_cross_check"]["max_deviation_from_trivial_B"], 1e-8)
        return result

    def test_4x3_phase_and_defect_recovery(self):
        self._check("4x3", 7.3)

    def test_3x4_phase_and_defect_recovery(self):
        self._check("3x4", -4.1)

    def test_cross_check_against_part1_reports_agreement(self):
        result = self._check("4x3", 2.0)
        part1_summary = {
            "s": {"median": self.s_rot}, "f": {"median": self.f_rot}, "r": {"median": self.r_rot},
            "delta_deg": {"median": float(result["rotating_side"]["delta_deg"])},
            "T": {"median": float(result["rotating_side"]["T"])},
        }
        diffs = cal.cross_check_against_part1(result, part1_summary, "median")
        for field in ("s", "f", "r", "delta_deg", "T"):
            self.assertAlmostEqual(diffs[field]["diff"], 0.0, places=6)

    def test_accepts_genuine_per_pixel_intensities(self):
        """Regression test for two real crashes found while building the
        calibration CLI (MMIE_ATOMIC_TARGETS.md Category 6):
        measure_phase_offset and measure_non_rotating_side_defects both
        used to force a direct float() cast that raised TypeError against
        genuine (n_frames, H, W) per-pixel intensities -- every other test
        in this file only ever exercises (n_frames,) scalar-per-frame
        arrays, which never hit that path."""

        mode, injected_offset_deg, n_outer, n_frames = "4x3", 6.25, 6, 60
        height, width = 3, 2
        rot_vec, outer_vec = _rotating_and_outer_vectors(mode, self.s_rot, self.f_rot, self.r_rot)
        identity = np.eye(4)

        def intensity_grid(true_c, outer_angle):
            base = _intensity(mode, identity, rot_vec, outer_vec, true_c, outer_angle)
            # Small per-pixel perturbation so the (H, W) maps aren't just a
            # broadcasted copy of one scalar -- a genuine, if tiny,
            # per-pixel signal to reduce via nanmedian.
            return base + 1e-3 * self.rng.standard_normal((height, width))

        raw_phase_angles = np.sort(self.rng.uniform(0, 360, n_frames))
        true_phase_angles = raw_phase_angles + injected_offset_deg
        phase_intensities = np.stack([intensity_grid(c, 0.0) for c in true_phase_angles], axis=0)

        outer_angles = list(np.linspace(0, 150, n_outer))
        per_outer_raw, per_outer_intensity = [], []
        for outer_angle in outer_angles:
            raw = np.sort(self.rng.uniform(0, 360, n_frames))
            true_c = raw + injected_offset_deg
            per_outer_raw.append(raw)
            per_outer_intensity.append(np.stack([intensity_grid(c, outer_angle) for c in true_c], axis=0))

        result = cal.run_single_arm_calibration(
            mode, raw_phase_angles, phase_intensities, outer_angles, per_outer_raw, per_outer_intensity
        )

        # C1' is a single mechanical/encoder-zero parameter, not a
        # per-pixel optical quantity (measure_phase_offset's docstring) --
        # confirm it reduced to a plain float, not an array that would
        # crash correct_revolution_angles' broadcast downstream.
        self.assertIsInstance(result["c1_prime_deg"], float)
        self.assertAlmostEqual(result["c1_prime_deg"], injected_offset_deg, places=1)

        s_map = np.asarray(result["rotating_side"]["s"])
        f_map = np.asarray(result["rotating_side"]["f"])
        self.assertEqual(s_map.shape, (height, width))
        self.assertEqual(f_map.shape, (height, width))
        self.assertTrue(np.allclose(s_map, self.s_rot, atol=0.02))
        self.assertTrue(np.allclose(f_map, self.f_rot, atol=0.02))

        # measure_non_rotating_side_defects' own independent crash (the
        # expected_b broadcast shape mismatch against a per-pixel e_mat).
        max_deviation = result["outer_side_cross_check"]["max_deviation_from_trivial_B"]
        self.assertIsInstance(max_deviation, float)
        self.assertTrue(np.isfinite(max_deviation))
        self.assertLess(max_deviation, 0.05)


class SingleArmCalibrationCLIIntegrationTests(unittest.TestCase):
    """End-to-end test against a REAL measure.py --dry-run --no-prompt
    session (MMIE_ATOMIC_TARGETS.md target 6.3). This is what actually
    caught the per-pixel crashes above -- the purely-synthetic-array tests
    never exercised measure.py's real Images/Logs/Config file format or
    genuine (H, W)-shaped camera frames. DATA_ROOT is monkeypatched to a
    temp directory for the duration of the test so it never writes into
    the real project's Data/ folder."""

    def test_load_and_run_against_real_dry_run_session(self):
        import tempfile

        sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "common"))
        import measure as measure_module

        original_data_root = measure_module.DATA_ROOT
        with tempfile.TemporaryDirectory() as tmp_dir:
            measure_module.DATA_ROOT = Path(tmp_dir)
            try:
                exit_code = measure_module.run_fresh_session(
                    "4x3", "continuous", "cal_cli_test", dry_run=True, no_prompt=True
                )
            finally:
                measure_module.DATA_ROOT = original_data_root

            self.assertEqual(exit_code, 0)
            run_dirs = list(Path(tmp_dir).glob("*_cal_cli_test_*"))
            self.assertEqual(len(run_dirs), 1)

            result = cal.load_and_run_single_arm_calibration(run_dirs[0])
            self.assertEqual(result["mode"], "4x3")
            self.assertIsInstance(result["c1_prime_deg"], float)
            self.assertTrue(np.isfinite(result["c1_prime_deg"]))
            # measure.py's dry-run camera is a generic synthetic gradient,
            # not a physics-aware polarimetry model (unlike Section II's
            # DryRunOpticalBench) -- DRY_RUN_WALKTHROUGH.md documents this
            # explicitly, so the recovered s/f/r below are NOT expected to
            # be physically meaningful. This test only confirms the real
            # file-format loader and the full calibration pipeline run to
            # completion without raising -- the physics itself is already
            # validated against known ground truth by the tests above.
            for key in ("s", "f", "p", "r", "delta_deg", "T"):
                self.assertIn(key, result["rotating_side"])


class ContinuousSingleArmMeasurementWrapperTests(unittest.TestCase):
    """continuous_single_arm_measurement.py -- Section IV's own capture
    main, a thin wrapper around common/measure.py (this project's "each
    section folder visibly contains both its own mains" convention).
    DATA_ROOT is monkeypatched to a temp directory so this never writes
    into the real project's Data/ folder."""

    def test_presets_continuous_acquisition_restricted_to_3x4_4x3(self):
        import json
        import tempfile

        sys.path.insert(0, str(Path(__file__).resolve().parent))
        import continuous_single_arm_measurement as wrapper

        # 4x4 is Section V's mode, not this section's -- confirm the
        # wrapper's own --mode choices genuinely exclude it.
        with self.assertRaises(SystemExit):
            original_argv = sys.argv
            sys.argv = ["continuous_single_arm_measurement.py", "--mode", "4x4"]
            try:
                wrapper.parse_args()
            finally:
                sys.argv = original_argv

        original_data_root = wrapper.measure.DATA_ROOT
        original_argv = sys.argv
        with tempfile.TemporaryDirectory() as tmp_dir:
            wrapper.measure.DATA_ROOT = Path(tmp_dir)
            sys.argv = [
                "continuous_single_arm_measurement.py", "--mode", "4x3", "--dry-run", "--no-prompt",
                "--run-label", "wraptest4",
            ]
            try:
                exit_code = wrapper.main()
            finally:
                sys.argv = original_argv
                wrapper.measure.DATA_ROOT = original_data_root

            self.assertEqual(exit_code, 0)
            run_dirs = list(Path(tmp_dir).glob("*_wraptest4_*"))
            self.assertEqual(len(run_dirs), 1)
            config = json.loads((run_dirs[0] / "Config" / "experiment_config.json").read_text())
            self.assertEqual(config["acquisition_type"], "continuous")
            self.assertEqual(config["mode"], "4x3")


if __name__ == "__main__":
    unittest.main()
