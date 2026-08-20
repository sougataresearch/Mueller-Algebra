# Section IV — single-arm continuous reconstruction (validation stepping-stone)

**Question this answers:** with only one QWP spinning continuously (the
other arm's plain polarizer stepped between revolutions), what is the
sample's Mueller matrix? A faster, semi-automated method than Section
III's static grid — and a stepping-stone toward Section V's full
dual-arm method below.

| File | What it is |
|---|---|
| `continuous_single_arm_reconstruction.py` | Per-revolution Fourier fit + E/B/F reconstruction (Hauge Eq. 41-50). |
| `continuous_single_arm_calibration.py` | Built-in phase-offset (C1′) and rotating-QWP-defect calibration (Hauge Sec. IV.C). |
| `test_continuous_single_arm.py` | Synthetic round-trip tests (see Testing, below). |

Covers `measure.py`'s **3x4 and 4x3 continuous** modes — the two modes
that have exactly one QWP in the beam (see `../common/README.md`'s mode
table). 3x4 spins `PSG_QWP` (the QWP plays the *generator* role); 4x3
spins `PSA_QWP` (the *analyzer* role, matching Hauge's own convention
directly). One function handles both, mirror-symmetric — not duplicated.

## The math, briefly

1. **Per-revolution Fourier fit**: at each outer-angle step, fit the
   spinning QWP's many frames (against their *real* logged angles, not an
   assumed uniform grid) to `R(C') = A0+A2cos(2C')+B2sin(2C')+A4cos(4C')+B4sin(4C')`.
2. **Recover a 4-vector** at that outer step from those 5 coefficients,
   using the rotating QWP's *real* s,f,r from Section II (Eq. 43).
3. **Second Fourier fit**: repeat across every outer-angle step, then fit
   *those* recovered vectors against the outer angle → the 4×5 matrix
   `E`. Fit with the REDUCED 3-term model (`1, cos(2A), sin(2A)` — no
   `cos(4A)`/`sin(4A)` terms) whenever the outer side is a plain polarizer
   (always true for 3x4/4x3): its own vector formula has zero cos4A/sin4A
   dependence, so the "extra" two terms of the full 5-term model aren't
   just unnecessary, they make the fit *underdetermined* if there are
   fewer than 5 outer steps — and `measure.py`'s own `OUTER_ANGLES_DEG`
   default is 3 (see "A bug found and fixed" below).
4. **Known matrix `B`** comes from the *other* (stepped, no-QWP) arm's own
   vector formula — a plain polarizer, so `B` is fixed and simple.
   `F = pinv(B)`, `M = E·F` (transposed if the QWP played the generator
   role, per step 4x3-vs-3x4 above).

Same recoverable-sub-block rule as Section III applies here too (3x4
misses the bottom row, 4x3 misses the rightmost column — `NaN`, not a
bug).

## This method's one hard requirement

The FIXED companion linear element (`PSG_Polarizer` for 3x4,
`PSA_Analyzer` for 4x3) must sit at its calibrated optical 0° — this
mirrors Hauge's own "P = A = 0°" premise for this section, and the
closed-form recovery formulas above are only valid at that reference.

## A bug found and fixed: the outer-angle fit was silently wrong at the default config

Before this fix, `fit_outer_fourier` always fit the full 5-parameter
model regardless of how many outer angles were given. `measure.py`'s own
`OUTER_ANGLES_DEG` default is `[0.0, 45.0, 90.0]` — only 3 angles. Fitting
5 unknowns from 3 equations is an **underdetermined system**, and
`numpy.linalg.lstsq` does not detect or warn about this — it silently
returns *a* solution (its minimum-norm one), not the correct one.
Confirmed before the fix: reconstructing with the literal default config
gave a max Mueller-matrix error of 0.59 instead of machine precision.

The fix uses the reduced 3-parameter model whenever the outer side has no
QWP (the only case 3x4/4x3 exercise) — analytically exact, not an
approximation, and needs only 3 outer angles, matching the default
config. More outer angles beyond 3 still reduce noise further, the same
way more revolution-frames do (verified in testing).

## Built-in calibration (a separate, sample-absent run)

`continuous_single_arm_calibration.py` finds the rotating QWP's phase
reference `C1′` (Hauge Eq. 57 — same idea as Section II's null search, but
recovered from the Fourier coefficients instead of a physical null) and
its own s,f,r independently of Section II, from one revolution at the
reference config. Its output is in the *same* `{s,f,r,delta_deg,T}` schema
as Section II's `qwp_calibration.py`, specifically so you can cross-check
the two — `cross_check_against_part1()` diffs them directly. Agreement is
confidence in both calibrations; disagreement flags something to
investigate (alignment drift, a bad null, camera nonlinearity).

Has its own CLI now (`--compare-to`): point it at a *sample-absent*
`measure.py` 3x4/4x3 continuous-mode session — its `OUTER_ANGLES_DEG` must
include `0.0`, since the phase-reference revolution Eq. 57/58 need
(fixed-side + outer axis both at optical 0) is physically the same
configuration as that session's own outer=0 step, and is read from it
directly rather than requiring a second, separately-captured revolution
(see `find_zero_outer_step()`'s docstring).

## Output

Reconstruction (`continuous_single_arm_reconstruction.py`): same schema as
Section III: `Results/mueller_matrix.npy` +
`Results/mueller_matrix_summary.json`.

Calibration cross-check (`continuous_single_arm_calibration.py`):
`Results/single_arm_calibration_cross_check.json` — the recovered `C1′`,
rotating-side `{s,f,r,delta_deg,T}`, source response, outer-side
cross-check diagnostic, and (with `--compare-to`) the diff against Section
II's numbers for the same QWP.

## Running it

```powershell
python ..\common\measure.py --mode 4x3 --acquisition continuous --dry-run --no-prompt --run-label sample2
python continuous_single_arm_reconstruction.py "..\Data\2026-08-18_sample2_01" `
    --rotating-calibration-dir "..\Data\QWP_Calibration\2026-08-18_PSG_QWPandPSA_QWP_01"

# Built-in calibration cross-check (separate, sample-absent session):
python ..\common\measure.py --mode 4x3 --acquisition continuous --dry-run --no-prompt --run-label calcheck4
python continuous_single_arm_calibration.py "..\Data\2026-08-18_calcheck4_01" `
    --compare-to "..\Data\QWP_Calibration\2026-08-18_PSG_QWPandPSA_QWP_01\Config\calibration_result.json"
```

## Testing

```powershell
python -m unittest test_continuous_single_arm -v
```

Synthetic round-trip for both 3x4 and 4x3: known M + known QWP defects →
synthetic per-frame intensities (real, non-uniform logged angles) →
reconstruction → known M recovered to near machine precision, noise
degrades gracefully. Also validates the calibration module's C1′ and
s,f,r recovery against injected ground truth, the cross-check-against-
Section-II report, a direct regression test at `measure.py`'s literal
`OUTER_ANGLES_DEG` default (`[0.0, 45.0, 90.0]`, the exact bug above), that
more outer angles further reduce noise, and `fit_outer_fourier`'s
underdetermined-system guard. No hardware required. (This test file is
the one place that reaches across section folders — it reuses Section
III's own generator/analyzer vector formulas to build its synthetic
forward model, since Section IV's physics is explicitly built on those
same equations.)
