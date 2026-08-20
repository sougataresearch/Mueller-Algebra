# Section V — dual-arm continuous reconstruction (production path)

**Question this answers:** with *both* QWPs spinning simultaneously at a
locked ratio (5:1), what is the sample's Mueller matrix, in a single
combined Fourier decomposition? This is **the production method** —
fastest, most-overdetermined, highest priority of the four sections.

| File | What it is |
|---|---|
| `continuous_dual_arm_measurement.py` | **Capture main** — thin wrapper around `../common/measure.py`, fixed to `--mode 4x4 --acquisition continuous`. |
| `continuous_dual_arm_reconstruction.py` | **Reconstruction main** — the 25-coefficient Fourier fit + full 16-element inversion (Hauge Eq. 62-66). |
| `continuous_dual_arm_calibration.py` | Built-in phase-origin (C1, C1′) and both-QWP-defect calibration (Hauge Sec. V.B), with its own `--compare-to` CLI. |
| `test_continuous_dual_arm.py` | Synthetic round-trip tests (see Testing, below). |

Covers `measure.py`'s **4x4 continuous** mode: `PSG_QWP` and `PSA_QWP`
spin together with `C' - C1' = 5*(C - C1)` (both polarizers fixed at
their calibrated zero).

## The math, briefly

1. **One Fourier fit**, directly against the real logged `(C, C')` angle
   pairs (no assumed 5:1-locked uniform grid): 25 coefficients — a
   constant `a0` plus 12 harmonics `theta_1..theta_12` (each with its own
   cos/sin amplitude), covering every combination of `2C, 4C, 2C', 4C` and
   their sums/differences.
2. **Direct inversion** of those 25 numbers to the 16 Mueller-matrix
   elements (Eq. 65) — closed-form, no iteration.
3. **9 consistency-check relationships** (Eq. 66) reported alongside the
   result — e.g. `a7+a3` should equal `f·s'·(M01+M11)` — as diagnostics
   only, never fed back into the inversion. Large deviations flag noise,
   misalignment, or a bad 5:1 lock.

All 16 elements are recoverable in this mode (no structural `NaN`s, unlike
Sections III/IV) — both arms have a QWP, so nothing is structurally blind.

## N-frame requirement (and a bug found and fixed)

The 25-coefficient fit needs at least 25 frames — fewer is an
**underdetermined system** that `numpy.linalg.lstsq` does not detect or
warn about (confirmed: 12 frames, from an unusually coarse
`CAPTURE_ANGLE_STEP_DEG` of 30°, gave a max Mueller-matrix error of 1.48
instead of machine precision — the same bug class found in Section IV's
outer-angle fit). `fit_dual_arm_fourier` now raises `ValueError` if given
fewer than 25 frames rather than silently returning a wrong answer.

Under normal operation this is nowhere close to being hit —
`CAPTURE_ANGLE_STEP_DEG`'s 1° default gives 360 frames per revolution,
already 14x the minimum — but an unusually coarse step size or a
truncated/interrupted acquisition could reach it. More frames beyond the
minimum keep reducing noise the same way in every other section
(verified in testing).

## Built-in calibration (a separate, sample-absent run)

With the sample removed (M = I), only the even harmonics up to the 10th
survive with real weight, and *every* `b_j` should be exactly zero — a
strong sanity check on its own (real air data with a large `b_j` means
misalignment). The two largest (4th, 6th) harmonics fix the phase origins
`C1, C1'` (Eq. 75, with 2nd/8th/10th as an independent consistency check);
once those are known, every raw coefficient phase-corrects via Eq. 77, and
`s,f,s',f'` for *both* QWPs fall out directly (Eq. 78/79) — independently
of Section II. Same `{s,f,r,delta_deg,T}` output schema as Section II, so
`cross_check_against_part1()` diffs the two directly (agreement =
confidence in both; disagreement = something to investigate).

Has its own CLI now (`--compare-to`): point it at a *sample-absent*
`measure.py` 4x4 continuous-mode session. Unlike Section IV, no separate
phase-reference revolution needs to be located — C1/C1′ both come from
one 25-coefficient fit of the whole session, so the run directory's own
`(C, C')` log feeds `run_dual_arm_calibration()` directly.

## Output

Reconstruction (`continuous_dual_arm_reconstruction.py`): same schema as
Sections III/IV: `Results/mueller_matrix.npy` +
`Results/mueller_matrix_summary.json` (the latter also carries the 9
consistency-check diagnostics).

Calibration cross-check (`continuous_dual_arm_calibration.py`):
`Results/dual_arm_calibration_cross_check.json` — the recovered `C1,C1′`,
both QWPs' `{s,f,r,delta_deg,T}`, source response, raw-`b_j` misalignment
check, and (with `--compare-to`) the diff against Section II's numbers for
both QWPs.

## Running it

`continuous_dual_arm_measurement.py` and
`..\common\measure.py --mode 4x4 --acquisition continuous` are the same
acquisition, callable either way — the former lives in this folder
specifically so this section visibly has both of its own mains:

```powershell
python continuous_dual_arm_measurement.py --dry-run --no-prompt --run-label sample3
# equivalent: python ..\common\measure.py --mode 4x4 --acquisition continuous --dry-run --no-prompt --run-label sample3

python continuous_dual_arm_reconstruction.py "..\Data\2026-08-18_sample3_01" `
    --psg-calibration-dir "..\Data\QWP_Calibration\2026-08-18_PSG_QWPandPSA_QWP_01" `
    --psa-calibration-dir "..\Data\QWP_Calibration\2026-08-18_PSG_QWPandPSA_QWP_01"

# Built-in calibration cross-check (separate, sample-absent session):
python ..\common\measure.py --mode 4x4 --acquisition continuous --dry-run --no-prompt --run-label calcheck5
python continuous_dual_arm_calibration.py "..\Data\2026-08-18_calcheck5_01" `
    --compare-to "..\Data\QWP_Calibration\2026-08-18_PSG_QWPandPSA_QWP_01\Config\calibration_result.json"
```

## Testing

```powershell
python -m unittest test_continuous_dual_arm -v
```

Synthetic round-trip: known M + known QWP defects → synthetic per-frame
intensities at a real (phase-offset, 5:1-locked) angle trajectory →
reconstruction → known M recovered to near machine precision, noise
degrades gracefully, all 9 consistency-check relationships hold exactly at
the noiseless recovery. Also validates the calibration module's C1/C1′ and
s,f,s',f' recovery against injected ground truth, the
cross-check-against-Section-II report, the below-25-frames
underdetermined-system guard (and that exactly 25 is accepted), and that
more frames beyond the minimum further reduce noise. No hardware
required, and this module is fully self-contained (no cross-section
imports).
