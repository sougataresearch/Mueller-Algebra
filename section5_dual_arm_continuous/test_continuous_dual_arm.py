"""Synthetic round-trip tests for continuous_dual_arm_reconstruction.py and
continuous_dual_arm_calibration.py (Section V: the 4x4 dual rotating-
compensator method -- the production reconstruction path, priority per the
project spec). No hardware, no motors, no camera required.
"""

from __future__ import annotations

import unittest

import numpy as np

import continuous_dual_arm_calibration as cal
import continuous_dual_arm_reconstruction as dual


def _random_mueller(rng: np.random.Generator) -> np.ndarray:
    m = np.eye(4) + 0.15 * rng.standard_normal((4, 4))
    m[0, 0] = 1.0
    return m


class DualArmReconstructionRoundTripTests(unittest.TestCase):
    def setUp(self) -> None:
        self.rng = np.random.default_rng(3)
        self.s, self.f = 0.015, 0.488
        self.r = np.sqrt(1 - (1 - 2 * self.f) ** 2 - self.s**2)
        self.s_prime, self.f_prime = -0.02, 0.493
        self.r_prime = np.sqrt(1 - (1 - 2 * self.f_prime) ** 2 - self.s_prime**2)

    def _check(self, n_frames=2000, noise=0.0, c1=3.7, c1_prime=-2.1):
        m_true = _random_mueller(self.rng)
        coeffs_true = dual.forward_coefficients(m_true, self.s, self.f, self.r, self.s_prime, self.f_prime, self.r_prime)

        t = np.linspace(0, 1, n_frames)
        c = c1 + t * 360 * 4
        cp = c1_prime + 5 * (c - c1)  # 5:1 lock
        r = dual.evaluate_intensity(coeffs_true, c, cp)
        if noise:
            r = r + noise * self.rng.standard_normal(n_frames)

        fitted = dual.fit_dual_arm_fourier(c, cp, r)
        m_rec = dual.invert_coefficients(fitted, self.s, self.f, self.r, self.s_prime, self.f_prime, self.r_prime)
        m_norm, trace_mtm, _ = dual.normalize_mueller_matrix(m_rec)
        m_true_norm = m_true / m_true[0, 0]
        err = np.max(np.abs(m_norm - m_true_norm))
        tol = 1e-6 if not noise else 0.5
        self.assertLess(err, tol, f"noise={noise}")
        return m_norm, m_true_norm

    def test_exact_recovery_noiseless(self):
        self._check()

    def test_exact_recovery_different_phase_and_seed(self):
        self._check(c1=-12.0, c1_prime=8.5)

    def test_graceful_noise_degradation(self):
        self._check(noise=0.02)

    def test_graceful_noise_degradation_higher(self):
        self._check(noise=0.05)

    def test_full_16_elements_recovered(self):
        """README: 4x4 recovers the full 16-entry matrix -- no NaNs."""

        m_norm, _ = self._check()
        self.assertFalse(np.any(np.isnan(m_norm)))

    def test_consistency_diagnostics_near_zero_at_exact_recovery(self):
        m_true = _random_mueller(self.rng)
        coeffs_true = dual.forward_coefficients(m_true, self.s, self.f, self.r, self.s_prime, self.f_prime, self.r_prime)
        m_rec = dual.invert_coefficients(coeffs_true, self.s, self.f, self.r, self.s_prime, self.f_prime, self.r_prime)
        diag = dual.consistency_diagnostics(coeffs_true, m_rec, self.s, self.f, self.r, self.s_prime, self.f_prime, self.r_prime)
        for key, value in diag.items():
            if isinstance(value, dict):
                self.assertAlmostEqual(value["lhs"], value["rhs"], places=8, msg=key)
            else:
                # a1/b4/a5/b6 are proportional to s,s' -- small for a
                # near-ideal QWP, but not exactly zero; just bound them.
                self.assertLess(abs(value), 0.1, msg=key)

    def test_realizability_diagnostic_on_identity(self):
        coeffs_true = dual.forward_coefficients(np.eye(4), self.s, self.f, self.r, self.s_prime, self.f_prime, self.r_prime)
        t = np.linspace(0, 1, 2000)
        c, cp = t * 360 * 4, 5 * t * 360 * 4
        r = dual.evaluate_intensity(coeffs_true, c, cp)
        fitted = dual.fit_dual_arm_fourier(c, cp, r)
        m_rec = dual.invert_coefficients(fitted, self.s, self.f, self.r, self.s_prime, self.f_prime, self.r_prime)
        m_norm, trace_mtm, is_realizable = dual.normalize_mueller_matrix(m_rec)
        self.assertAlmostEqual(float(trace_mtm), 4.0, places=4)
        self.assertTrue(bool(is_realizable))

    def test_rejects_underdetermined_fit_below_25_frames(self):
        """Regression test: an unusually coarse CAPTURE_ANGLE_STEP_DEG
        (e.g. 30 deg -> 12 frames/revolution) or a truncated acquisition
        can produce fewer than the 25 frames this model needs. Confirmed
        without the guard: 12 frames gave a max Mueller-matrix error of
        1.48 (garbage), not machine precision -- lstsq doesn't detect an
        underdetermined system on its own."""

        coeffs_true = dual.forward_coefficients(np.eye(4), self.s, self.f, self.r, self.s_prime, self.f_prime, self.r_prime)
        t = np.linspace(0, 1, 12)
        c, cp = t * 360, 5 * t * 360
        r = dual.evaluate_intensity(coeffs_true, c, cp)
        with self.assertRaises(ValueError):
            dual.fit_dual_arm_fourier(c, cp, r)

    def test_accepts_exactly_25_frames(self):
        coeffs_true = dual.forward_coefficients(np.eye(4), self.s, self.f, self.r, self.s_prime, self.f_prime, self.r_prime)
        t = np.linspace(0, 1, 25)
        c, cp = t * 360, 5 * t * 360
        r = dual.evaluate_intensity(coeffs_true, c, cp)
        fitted = dual.fit_dual_arm_fourier(c, cp, r)  # must not raise
        self.assertIn("a12", fitted)

    def test_more_frames_reduce_noise(self):
        """More-than-minimum frames per revolution lowers the noise on
        the recovered M the same way it does in Sections II and IV."""

        m_true = _random_mueller(self.rng)
        coeffs_true = dual.forward_coefficients(m_true, self.s, self.f, self.r, self.s_prime, self.f_prime, self.r_prime)
        m_true_norm = m_true / m_true[0, 0]
        noise = 0.05

        def recovered_error(n_frames: int) -> float:
            t = np.linspace(0, 1, n_frames)
            c, cp = t * 360, 5 * t * 360
            r = dual.evaluate_intensity(coeffs_true, c, cp) + noise * self.rng.standard_normal(n_frames)
            fitted = dual.fit_dual_arm_fourier(c, cp, r)
            m_rec = dual.invert_coefficients(fitted, self.s, self.f, self.r, self.s_prime, self.f_prime, self.r_prime)
            m_norm, _, _ = dual.normalize_mueller_matrix(m_rec)
            return float(np.mean(np.abs(m_norm - m_true_norm)))

        errors_few = [recovered_error(30) for _ in range(15)]
        errors_many = [recovered_error(360) for _ in range(15)]
        self.assertLess(np.mean(errors_many), np.mean(errors_few))


class DualArmCalibrationRoundTripTests(unittest.TestCase):
    def setUp(self) -> None:
        self.rng = np.random.default_rng(11)
        self.s, self.f = 0.017, 0.487
        self.r = np.sqrt(1 - (1 - 2 * self.f) ** 2 - self.s**2)
        self.s_prime, self.f_prime = -0.021, 0.494
        self.r_prime = np.sqrt(1 - (1 - 2 * self.f_prime) ** 2 - self.s_prime**2)

    def _check(self, c1_true, c1_prime_true, n_frames=3000):
        coeffs_true = dual.forward_coefficients(np.eye(4), self.s, self.f, self.r, self.s_prime, self.f_prime, self.r_prime)
        raw_c = self.rng.uniform(0, 360 * 3, n_frames)
        raw_cp = 5 * raw_c
        true_c = raw_c + c1_true
        true_cp = raw_cp + c1_prime_true
        r = dual.evaluate_intensity(coeffs_true, true_c, true_cp)

        result = cal.run_dual_arm_calibration(raw_c, raw_cp, r)
        self.assertAlmostEqual(result["c1_deg"], c1_true, places=6)
        self.assertAlmostEqual(result["c1_prime_deg"], c1_prime_true, places=6)
        self.assertAlmostEqual(float(result["psg_qwp"]["s"]), self.s, places=6)
        self.assertAlmostEqual(float(result["psg_qwp"]["f"]), self.f, places=6)
        self.assertAlmostEqual(float(result["psa_qwp"]["s"]), self.s_prime, places=6)
        self.assertAlmostEqual(float(result["psa_qwp"]["f"]), self.f_prime, places=6)
        return result

    def test_zero_phase_offset(self):
        self._check(0.0, 0.0)

    def test_positive_phase_offset(self):
        self._check(5.0, -3.0)

    def test_negative_phase_offset(self):
        self._check(-12.0, 8.5)

    def test_phase_consistency_checks_agree(self):
        result = self._check(5.0, -3.0)
        consistency = result["phase_consistency"]
        self.assertAlmostEqual(consistency["phi4_measured"], consistency["phi4_predicted"], places=6)
        self.assertAlmostEqual(consistency["phi6_measured"], consistency["phi6_predicted"], places=6)
        self.assertAlmostEqual(consistency["phi8_measured"], consistency["phi8_predicted"], places=6)

    def test_cross_check_against_part1_reports_agreement(self):
        result = self._check(0.0, 0.0)
        part1_summaries = {
            "PSG_QWP": {
                "s": {"median": self.s}, "f": {"median": self.f}, "r": {"median": self.r},
                "delta_deg": {"median": float(result["psg_qwp"]["delta_deg"])},
                "T": {"median": float(result["psg_qwp"]["T"])},
            },
            "PSA_QWP": {
                "s": {"median": self.s_prime}, "f": {"median": self.f_prime}, "r": {"median": self.r_prime},
                "delta_deg": {"median": float(result["psa_qwp"]["delta_deg"])},
                "T": {"median": float(result["psa_qwp"]["T"])},
            },
        }
        diffs = cal.cross_check_against_part1(result, part1_summaries, "median")
        for target in ("PSG_QWP", "PSA_QWP"):
            for field in ("s", "f", "r", "delta_deg", "T"):
                self.assertAlmostEqual(diffs[target][field]["diff"], 0.0, places=6)


if __name__ == "__main__":
    unittest.main()
