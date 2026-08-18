"""Synthetic round-trip tests for discrete_reconstruction.py (Section III).

For every mode (3x3/3x4/4x3/4x4), generates synthetic "measured" intensities
from a KNOWN Mueller matrix and KNOWN s,f,r using the SAME forward vector
formulas the module itself implements, feeds them through
reconstruct_mueller_matrix(), and confirms the known M is recovered to near
machine precision (noiseless, both exact-4-state and overdetermined grids,
both scalar and per-pixel calibration) and degrades gracefully under added
noise. This is the primary correctness gate (Part 5 spec) -- no hardware,
no motors, no camera required.
"""

from __future__ import annotations

import unittest

import numpy as np

import discrete_reconstruction as dr


def _random_mueller(rng: np.random.Generator) -> np.ndarray:
    m = np.eye(4) + 0.15 * rng.standard_normal((4, 4))
    m[0, 0] = 1.0
    return m


def _broadcast_vector(vector: np.ndarray, pixel_shape: tuple) -> np.ndarray:
    """A vector may come back scalar (arity,) even when the OTHER side of
    this state carries real per-pixel maps (e.g. a no-QWP side, whose
    formula never touches calibration at all) -- pad it to the shared
    pixel_shape so einsum below broadcasts cleanly."""

    if not pixel_shape or vector.ndim > 1:
        return vector
    return np.broadcast_to(vector.reshape(vector.shape + (1,) * len(pixel_shape)), vector.shape + pixel_shape)


def _forward_intensities(mode, first_angles, second_angles, fixed, m_true, psg_cal, psa_cal, pixel_shape=()):
    rows_needed, cols_needed = dr._vector_arity(mode)
    g_vectors = [
        _broadcast_vector(dr.make_generator_vector(mode, a, fixed, psg_cal)[:cols_needed], pixel_shape)
        for a in first_angles
    ]
    d_vectors = [
        _broadcast_vector(dr.make_analyzer_vector(mode, a, fixed, psa_cal)[:rows_needed], pixel_shape)
        for a in second_angles
    ]
    r = np.empty((len(second_angles), len(first_angles)) + pixel_shape)
    m_sub = m_true[:rows_needed, :cols_needed]
    for i, d in enumerate(d_vectors):
        for j, g in enumerate(g_vectors):
            r[i, j] = np.einsum("a...,ab,b...->...", d, m_sub, g)
    return r


class DiscreteReconstructionRoundTripTests(unittest.TestCase):
    def setUp(self) -> None:
        self.rng = np.random.default_rng(0)

    def _check(self, mode, n_first, n_second, use_pixel=False, noise=0.0):
        table = dr.MODE_TABLE[mode]
        fixed = {}
        if table["psg_has_qwp"]:
            fixed["PSG_Polarizer"] = 12.3
        if table["psa_has_qwp"]:
            fixed["PSA_Analyzer"] = 7.7
        m_true = _random_mueller(self.rng)

        if use_pixel:
            shape = (3, 4)
            s_psg, f_psg = 0.02 * np.ones(shape), 0.49 * np.ones(shape)
            r_psg = np.sqrt(1 - (1 - 2 * f_psg) ** 2 - s_psg**2)
            s_psa, f_psa = -0.01 * np.ones(shape), 0.51 * np.ones(shape)
            r_psa = np.sqrt(1 - (1 - 2 * f_psa) ** 2 - s_psa**2)
        else:
            s_psg, f_psg = 0.02, 0.49
            r_psg = np.sqrt(1 - (1 - 2 * f_psg) ** 2 - s_psg**2)
            s_psa, f_psa = -0.01, 0.51
            r_psa = np.sqrt(1 - (1 - 2 * f_psa) ** 2 - s_psa**2)

        psg_cal = {"s": s_psg, "f": f_psg, "r": r_psg} if table["psg_has_qwp"] else None
        psa_cal = {"s": s_psa, "f": f_psa, "r": r_psa} if table["psa_has_qwp"] else None

        first_angles = list(np.linspace(0, 150, n_first))
        second_angles = list(np.linspace(0, 150, n_second))
        pixel_shape = (3, 4) if use_pixel else ()
        r = _forward_intensities(mode, first_angles, second_angles, fixed, m_true, psg_cal, psa_cal, pixel_shape)
        if noise:
            r = r + noise * self.rng.standard_normal(r.shape)

        m_rec = dr.reconstruct_mueller_matrix(mode, first_angles, second_angles, fixed, r, psg_cal, psa_cal)
        m_norm, trace_mtm, _ = dr.normalize_mueller_matrix(m_rec)
        m_true_norm = m_true / m_true[0, 0]

        diff = m_norm - (m_true_norm[None, None, :, :] if use_pixel else m_true_norm)
        err = np.nanmax(np.abs(diff))
        tol = 1e-6 if not noise else 0.5
        self.assertLess(err, tol, f"{mode} n_first={n_first} n_second={n_second} pixel={use_pixel} noise={noise}")

        # Diagnostics run without raising, and are finite wherever M is.
        self.assertEqual(trace_mtm.shape, m_norm.shape[:-2])

    def test_exact_four_state_all_modes(self):
        for mode in ("3x3", "3x4", "4x3", "4x4"):
            rows_needed, cols_needed = dr._vector_arity(mode)
            self._check(mode, cols_needed, rows_needed)

    def test_overdetermined_all_modes(self):
        for mode in ("3x3", "3x4", "4x3", "4x4"):
            rows_needed, cols_needed = dr._vector_arity(mode)
            self._check(mode, cols_needed + 3, rows_needed + 2)

    def test_overdetermined_per_pixel_calibration_all_modes(self):
        for mode in ("3x3", "3x4", "4x3", "4x4"):
            rows_needed, cols_needed = dr._vector_arity(mode)
            self._check(mode, cols_needed + 3, rows_needed + 2, use_pixel=True)

    def test_graceful_noise_degradation_all_modes(self):
        for mode in ("3x3", "3x4", "4x3", "4x4"):
            rows_needed, cols_needed = dr._vector_arity(mode)
            self._check(mode, cols_needed + 6, rows_needed + 6, noise=0.01)

    def test_3x3_recovers_only_top_left_block(self):
        """README: 3x3 recovers S0,S1,S2 sub-block only."""

        m_true = _random_mueller(self.rng)
        first_angles = list(np.linspace(0, 150, 6))
        second_angles = list(np.linspace(0, 150, 6))
        r = _forward_intensities("3x3", first_angles, second_angles, {}, m_true, None, None)
        m_rec = dr.reconstruct_mueller_matrix("3x3", first_angles, second_angles, {}, r, None, None)
        self.assertTrue(np.all(np.isnan(m_rec[3, :])))
        self.assertTrue(np.all(np.isnan(m_rec[:, 3])))
        self.assertFalse(np.any(np.isnan(m_rec[:3, :3])))

    def test_3x4_recovers_everything_except_bottom_row(self):
        m_true = _random_mueller(self.rng)
        fixed = {"PSG_Polarizer": 0.0}
        psg_cal = {"s": 0.02, "f": 0.49, "r": np.sqrt(1 - (1 - 2 * 0.49) ** 2 - 0.02**2)}
        first_angles = list(np.linspace(0, 150, 7))
        second_angles = list(np.linspace(0, 150, 6))
        r = _forward_intensities("3x4", first_angles, second_angles, fixed, m_true, psg_cal, None)
        m_rec = dr.reconstruct_mueller_matrix("3x4", first_angles, second_angles, fixed, r, psg_cal, None)
        self.assertTrue(np.all(np.isnan(m_rec[3, :])))
        self.assertFalse(np.any(np.isnan(m_rec[:3, :])))

    def test_4x3_recovers_everything_except_rightmost_column(self):
        m_true = _random_mueller(self.rng)
        fixed = {"PSA_Analyzer": 0.0}
        psa_cal = {"s": -0.01, "f": 0.51, "r": np.sqrt(1 - (1 - 2 * 0.51) ** 2 - 0.01**2)}
        first_angles = list(np.linspace(0, 150, 6))
        second_angles = list(np.linspace(0, 150, 7))
        r = _forward_intensities("4x3", first_angles, second_angles, fixed, m_true, None, psa_cal)
        m_rec = dr.reconstruct_mueller_matrix("4x3", first_angles, second_angles, fixed, r, None, psa_cal)
        self.assertTrue(np.all(np.isnan(m_rec[:, 3])))
        self.assertFalse(np.any(np.isnan(m_rec[:, :3])))

    def test_realizability_diagnostic_on_identity(self):
        first_angles = list(np.linspace(0, 150, 6))
        second_angles = list(np.linspace(0, 150, 6))
        fixed = {"PSG_Polarizer": 0.0, "PSA_Analyzer": 0.0}
        s, f = 0.0, 0.5
        r = np.sqrt(1 - (1 - 2 * f) ** 2 - s**2)
        cal = {"s": s, "f": f, "r": r}
        r_meas = _forward_intensities("4x4", first_angles, second_angles, fixed, np.eye(4), cal, cal)
        m_rec = dr.reconstruct_mueller_matrix("4x4", first_angles, second_angles, fixed, r_meas, cal, cal)
        m_norm, trace_mtm, is_realizable = dr.normalize_mueller_matrix(m_rec)
        self.assertAlmostEqual(float(trace_mtm), 4.0, places=6)
        self.assertTrue(bool(is_realizable))

    def test_more_states_reduce_noise(self):
        """Section III already generalizes to N > minimum states via
        reconstruct_mueller_matrix's automatic least-squares dispatch --
        this makes that noise-reduction benefit explicit and verified,
        the same way Section II/IV/V's own N-angle variants are."""

        rng = np.random.default_rng(42)
        mode = "4x4"
        fixed = {"PSG_Polarizer": 0.0, "PSA_Analyzer": 0.0}
        s, f = 0.02, 0.485
        r = np.sqrt(1 - (1 - 2 * f) ** 2 - s**2)
        cal = {"s": s, "f": f, "r": r}
        m_true = _random_mueller(rng)
        m_true_norm = m_true / m_true[0, 0]
        noise = 0.05

        def recovered_error(num_angles: int) -> float:
            angles = dr.suggest_angle_grid(mode, num_angles, fixed)
            r_meas = _forward_intensities(mode, angles, angles, fixed, m_true, cal, cal)
            r_meas = r_meas + noise * rng.standard_normal(r_meas.shape)
            m_rec = dr.reconstruct_mueller_matrix(mode, angles, angles, fixed, r_meas, cal, cal)
            m_norm, _, _ = dr.normalize_mueller_matrix(m_rec)
            return float(np.mean(np.abs(m_norm - m_true_norm)))

        errors_minimum = [recovered_error(4) for _ in range(15)]
        errors_many = [recovered_error(12) for _ in range(15)]
        self.assertLess(np.mean(errors_many), np.mean(errors_minimum))


class SuggestAngleGridTests(unittest.TestCase):
    """suggest_angle_grid: a convenience generator (paralleling Section
    II's least_squares_calibration_angles) for picking an N-angle grid
    that won't alias -- not a replacement for measure.py's own pre-flight
    check_angle_grid_rank against your real, final angle choice."""

    def test_full_rank_for_every_mode_at_several_n(self):
        for mode in ("3x3", "3x4", "4x3", "4x4"):
            fixed = {}
            table = dr.MODE_TABLE[mode]
            if table["psg_has_qwp"]:
                fixed["PSG_Polarizer"] = 0.0
            if table["psa_has_qwp"]:
                fixed["PSA_Analyzer"] = 0.0
            rows_needed, cols_needed = dr._vector_arity(mode)
            required_rank = rows_needed * cols_needed
            minimum = max(rows_needed, cols_needed)
            for num_angles in sorted({minimum, minimum + 1, 6, 8, 12}):
                with self.subTest(mode=mode, num_angles=num_angles):
                    grid = dr.suggest_angle_grid(mode, num_angles, fixed)
                    self.assertEqual(len(grid), num_angles)
                    self.assertGreaterEqual(dr._ideal_grid_rank(mode, grid, fixed), required_rank)

    def test_reproducible_across_calls(self):
        fixed = {"PSG_Polarizer": 0.0, "PSA_Analyzer": 0.0}
        first = dr.suggest_angle_grid("4x4", 4, fixed)
        second = dr.suggest_angle_grid("4x4", 4, fixed)
        self.assertEqual(first, second)

    def test_rejects_too_few_angles(self):
        with self.assertRaises(ValueError):
            dr.suggest_angle_grid("4x4", 3)

    def test_0_45_90_135_is_the_known_aliasing_trap_for_arity_four(self):
        """Regression context: this exact 4-angle, 45-deg-spaced grid
        aliases to rank 9 (not the 12 needed) for an ideal QWP on a 3x4
        arity-4 side, regardless of phase offset -- which is exactly why
        suggest_angle_grid falls back to a pseudo-random search at N=4
        for the QWP-bearing modes instead of only ever trying evenly-
        spaced candidates."""

        fixed = {"PSG_Polarizer": 0.0}
        rank = dr._ideal_grid_rank("3x4", [0.0, 45.0, 90.0, 135.0], fixed)
        self.assertLess(rank, 12)


if __name__ == "__main__":
    unittest.main()
