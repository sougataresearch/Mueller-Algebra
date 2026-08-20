# Detailed Design — MMIE

Unlike a forward-looking design doc for an unbuilt project, most of this
records design decisions **already made and implemented** across
Sections II–V, plus the handful of things still genuinely open. See
`MMIE_ATOMIC_TARGETS.md` for target-by-target status.

## 1. Three acquisition strategies, not one refined over time

Discrete (III), single-arm continuous (IV), and dual-arm continuous (V)
are three genuinely different measurement strategies for the same
underlying physics, not three iterations of the same idea:

- **Discrete** grids two motors to a static angle set, one image per stop
  — simplest to reason about, supports every mode (3×3/3×4/4×3/4×4), but
  slowest (many separate moves+captures).
- **Single-arm continuous** spins one QWP continuously while the other
  arm's plain polarizer is stepped between revolutions — faster, but only
  applies to 3×4/4×3 (a fixed linear element can't supply enough
  polarization diversity on its own at a single position, so the
  non-QWP side must still step; see `continuous_single_arm_reconstruction.py`'s
  docstring).
- **Dual-arm continuous** spins both QWPs together at a locked 5:1 ratio
  — fastest, most-overdetermined, the only mode with no structural blind
  spot (all 16 elements recoverable, no `NaN`s). This is the production
  path (`README.md`, `PRD.md`).

All three are implemented, tested, and kept as parallel options — not
collapsed into "just use Section V," since discrete's simplicity has
independent value as a cross-check / stepping-stone (`common/README.md`'s
own framing of Section IV as "a stepping-stone toward Section V").

## 2. Per-revolution then per-outer-angle Fourier fit (Section IV)

**Decided**: two-stage Fourier fit — first fit each revolution's frames
to a 5-term model (`A0+A2cos(2C')+B2sin(2C')+A4cos(4C')+B4sin(4C')`)
against the *real* logged angles (not an assumed uniform grid), recover a
4-vector per outer step, then fit *those* vectors against the outer angle
to build the 4×5 matrix `E`. This mirrors Hauge's own two-stage structure
(Eq. 41-50) rather than attempting a single combined fit.

**Bug found and fixed**: the outer-angle fit originally always used the
full 5-parameter model regardless of how many outer angles were given.
`measure.py`'s own `OUTER_ANGLES_DEG` default is `[0, 45, 90]` — only 3
angles, an **underdetermined system** for 5 unknowns.
`numpy.linalg.lstsq` does not detect or warn about this — it silently
returns *a* solution (its minimum-norm one), not the correct one.
Confirmed before the fix: reconstructing with the literal default config
gave a max Mueller-matrix error of 0.59 instead of machine precision. Fix:
use the analytically-exact reduced 3-parameter model
(`1, cos(2A), sin(2A)`) whenever the outer side has no QWP — always true
for 3×4/4×3, since a plain polarizer's own vector formula has zero
`cos(4A)`/`sin(4A)` dependence, so the extra two terms aren't just
unnecessary, they make the fit underdetermined below 5 outer angles. See
`decisions.md` ADR-006, permanent regression test in
`test_continuous_single_arm.py`.

## 3. 25-coefficient combined Fourier fit (Section V)

**Decided**: one combined fit against real logged `(C, C')` angle pairs
(not an assumed 5:1-locked uniform grid) to 25 coefficients (`a0` + 12
harmonics, each cos+sin), then closed-form inversion (Eq. 65) to the 16
Mueller elements — no iteration, matching Hauge Eq. 62-66 directly.

**Bug found and fixed**: the 25-coefficient fit needs at least 25 frames;
fewer is again an underdetermined system `lstsq` doesn't flag (confirmed:
12 frames from an unusually coarse `CAPTURE_ANGLE_STEP_DEG` of 30° gave a
max error of 1.48 instead of machine precision — the same bug class as
Section IV's). Fix: `fit_dual_arm_fourier` now raises `ValueError` below
25 frames rather than silently returning a wrong answer. Under normal
operation (`CAPTURE_ANGLE_STEP_DEG`'s 1° default → 360 frames/revolution)
this is nowhere close to being hit, but a coarse step or a truncated
acquisition could reach it. See `decisions.md` ADR-007.

## 4. Aliasing traps in angle-grid selection (Sections II and III)

Two independent, non-obvious aliasing failure modes, both found while
building this project and both now guarded against rather than left as a
footgun:

- **Section II**: evenly-spaced N=3 calibration angles alias (60° spacing
  makes `cos(2*60°)` and `cos(4*60°)` coincide) — `qwp_calibration.py`'s
  `least_squares_calibration_angles()` special-cases N=3 back to Hauge's
  own literal 0°/45°/90°; N≥4 is unaffected (verified full-rank for N=4
  through 15).
- **Section III**: the literal minimum 4-angle grid `0/45/90/135`
  (evenly 45°-spaced) aliases to rank 9 of the 12 needed for 3×4 — a
  resonance of the `cos(2θ)`/`cos(4θ)` structure itself, not dependent on
  which phase you start at, so every evenly-45°-spaced candidate fails
  regardless of offset. `suggest_angle_grid()` falls back to a
  deterministic (seeded, reproducible) search when this happens — only
  visible at the exact 4-angle minimum for 3×4/4×3/4×4; N≥5 always finds
  an evenly-spaced grid that works.

Both are permanent regression tests, not just documented prose — see
`testing.md`.

## 5. Failure Contract

Following the same discipline throughout: explicit exceptions naming
what's wrong, never a silent wrong answer.

- `MotorError`/`CameraError` (from `motor_communication.py`/
  `camera_communication.py`) on a real hardware failure, with
  retry-with-backoff already built into `measure.py`'s move/capture loop.
- `check_angle_grid_rank()` refuses to proceed (before any image is
  captured) if a *discrete*-mode angle grid is degenerate — catching the
  Section III aliasing trap above before a wasted acquisition, not after.
- `fit_outer_fourier`/`fit_dual_arm_fourier` raise `ValueError` on an
  underdetermined system (Sections IV/V bugs above) rather than returning
  `lstsq`'s minimum-norm non-answer.
- Sanity-check warnings (not hard failures) in `qwp_calibration.py`:
  `abs(s)>0.15` or `abs(f-0.5)>0.15` (bad null, not necessarily a bad
  QWP), `delta_deg` outside 80-100°, any pixel with `p^2+s^2>1` (clipped,
  count reported) — these print and, outside `--no-prompt`, ask the
  operator to confirm before continuing, rather than either silently
  proceeding or hard-failing on a borderline read.

## 6. Function signatures (textual)

```
qwp_calibration.run_calibration(targets, null_search_mode, dry_run, no_prompt,
                                 calibration_angle_mode, num_calibration_angles) -> int

discrete_reconstruction.reconstruct_mueller_matrix(run_dir, psg_calibration_dir,
                                                    psa_calibration_dir, roi, scalar_calibration) -> dict

continuous_single_arm_reconstruction.reconstruct(run_dir, rotating_calibration_dir,
                                                  roi, scalar_calibration) -> dict
continuous_single_arm_calibration.run_single_arm_calibration(mode, phase_ref_angles_deg,
    phase_ref_intensities, outer_angles_deg, per_outer_rotating_angles_deg,
    per_outer_intensities) -> dict
continuous_single_arm_calibration.cross_check_against_part1(single_arm_result,
    part1_summary, aggregation) -> dict

continuous_dual_arm_reconstruction.reconstruct(run_dir, psg_calibration_dir,
                                                psa_calibration_dir, roi, scalar_calibration) -> dict
continuous_dual_arm_calibration.run_dual_arm_calibration(...) -> dict   # mirrors single-arm's shape

continuous_single_arm_calibration.load_and_run_single_arm_calibration(run_dir) -> dict
continuous_dual_arm_calibration.load_and_run_dual_arm_calibration(run_dir) -> dict
```

`load_and_run_single_arm_calibration`/`load_and_run_dual_arm_calibration`
(added 2026-08-20, `MMIE_ATOMIC_TARGETS.md` Category 6) wire
`run_single_arm_calibration`/`run_dual_arm_calibration` to a real
sample-absent `Data/<run>/` continuous-mode folder, reusing the
reconstruction modules' own loaders. Both now have an `argparse` CLI
(`--compare-to`, `--roi`, `--aggregation`) — see the section READMEs'
"Built-in calibration" sections and `RECIPE.md`.

## 7. Open questions

- **Storage format**: `.npy` + JSON has been sufficient at this project's
  actual scale (one camera frame per reconstruction) — no reason to
  revisit unless frame sizes or sample counts grow enough to matter, which
  hasn't happened.
- ~~CLI for Section IV/V calibration cross-check~~ — done 2026-08-20, see
  §6 above and `decisions.md` ADR-011 (which also documents three real
  per-pixel bugs the CLI's own testing found and fixed).
- **Real-hardware validation** — every synthetic round-trip test confirms
  the math against its own stated forward model; nothing here has been
  run against an actual bench in this development environment. This is
  the single largest gap between "tests pass" and "known to work for
  real" — see `PRD.md`'s Risks and `troubleshooting.md`'s Anticipated
  Gotchas.
