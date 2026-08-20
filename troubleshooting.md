# Troubleshooting — MMIE

## Already-Solved Gotchas (documented in code, worth knowing)

### Section IV's outer-angle Fourier fit was silently wrong at `measure.py`'s own default config

`fit_outer_fourier` originally always fit the full 5-parameter model
regardless of how many outer angles were given. `measure.py`'s own
`OUTER_ANGLES_DEG` default is `[0.0, 45.0, 90.0]` — only 3 angles, an
underdetermined system for 5 unknowns. `numpy.linalg.lstsq` does not
detect or warn about this — it silently returns *a* solution (its
minimum-norm one), not the correct one. Confirmed before the fix:
reconstructing with the literal default config gave a max Mueller-matrix
error of 0.59 instead of machine precision. Fixed by using the
analytically-exact reduced 3-parameter model whenever the outer side has
no QWP (always true for 3×4/4×3). See `design.md` §2, `decisions.md`
ADR-006, permanent regression test in `test_continuous_single_arm.py`.

### Section V's 25-coefficient fit needs ≥25 frames, and nothing warned about fewer

Same underdetermined-fit bug class: fewer than 25 frames for the
25-coefficient dual-arm fit gave a max error of 1.48 instead of machine
precision (confirmed at 12 frames, from an unusually coarse
`CAPTURE_ANGLE_STEP_DEG` of 30°). Fixed: `fit_dual_arm_fourier` now raises
`ValueError` below 25 frames. Under normal operation (1° default step →
360 frames/revolution) this is nowhere close to being hit, but a coarse
step or a truncated/interrupted acquisition could reach it. See
`design.md` §3, `decisions.md` ADR-007.

### Section II's N=3 calibration-angle aliasing

Evenly-spaced 0°/45°/90° (Hauge's own literal 3-angle spec) works fine at
exactly N=3, but a *naive* generalization to "N evenly-spaced angles"
aliases at N=3 specifically (60° spacing makes `cos(2·60°)` and
`cos(4·60°)` coincide). `least_squares_calibration_angles()`
special-cases N=3 back to the literal 0/45/90; N≥4 is unaffected
(verified full-rank for N=4 through 15). See `design.md` §4.

### Section III's `0°/45°/90°/135°` grid aliases for 3×4/4×3/4×4 at the exact 4-angle minimum

The literal minimum, evenly-45°-spaced 4-angle grid aliases to rank 9 of
the 12 needed for 3×4 — a resonance of the `cos(2θ)`/`cos(4θ)` structure
itself, not dependent on starting phase, so every evenly-45°-spaced
candidate fails regardless of offset. `suggest_angle_grid()` falls back to
a deterministic (seeded, reproducible) search in this exact case; N≥5
always finds an evenly-spaced grid that works. See `design.md` §4.

### A "vectorized" function crashed on real per-pixel camera frames despite passing all its existing tests

While building the Section IV/V calibration CLI (`MMIE_ATOMIC_TARGETS.md`
Category 6), three functions that had only ever been unit-tested with 1-D
scalar-per-frame synthetic arrays crashed the instant they were run
against real `(H, W)` camera frames from an actual `measure.py --dry-run`
session: `continuous_single_arm_calibration.measure_phase_offset`,
`measure_non_rotating_side_defects` (a second, independent bug in the same
function: `expected_b`'s `(4,5)` shape didn't broadcast against a
per-pixel `e_mat`'s `(4,5,H,W)`), and
`continuous_dual_arm_calibration.solve_phase_origins`. All three forced a
premature `float(...)` cast on what turned out to be a genuine multi-pixel
array. Fixed by reducing each (a single mechanical/encoder-zero parameter,
not a spatially-varying optical quantity) to a scalar via `np.nanmedian`
before use — see `decisions.md` ADR-011, `rules.md` AI Coding Rule 4. Now
covered by a permanent per-pixel regression test in each section, plus a
new integration test that runs a real `measure.py --dry-run` session
(`DATA_ROOT` monkeypatched to a temp directory) rather than only
synthetic in-memory arrays.

### `Data/` always lands at the project root, regardless of current directory

Every acquisition/calibration script resolves its output root relative to
the project's own location, not the caller's current working directory —
run `python section4_single_arm_continuous\continuous_single_arm_reconstruction.py ...`
from anywhere and `Data/` still lands at the project root
(`common/README.md`, `README.md`). If you ever see output landing
somewhere unexpected, check what resolved the output root before assuming
a bug — this is deliberate, not accidental.

## Anticipated Gotchas (flagged ahead of real-hardware use)

- **Nothing here has been run against a real bench in this development
  environment.** Every synthetic round-trip test confirms the math
  against its own stated forward model — it cannot catch a genuine
  real-world effect the model doesn't include (true camera nonlinearity,
  actual misalignment beyond what dry-run's synthetic model represents).
  First real-hardware session should treat Section IV/V's built-in
  calibration cross-check against Section II as a genuine confidence
  check, not a formality — a real disagreement there is exactly the
  signal this feature exists to catch.
- **Continuous acquisition has no resume.** Unlike discrete mode
  (`--resume`), an interrupted continuous revolution or outer step
  restarts from scratch — by design, not a missing feature
  (`common/README.md`).
- **A missing Kinesis/IDS Peak SDK is reported, not fatal, in `--dry-run`.**
  `check_environment()`'s table will show `MISSING` lines for both in this
  (or any non-bench) development environment — that's expected and
  harmless for dry-run; it *does* hard-stop a real (non-dry-run) session
  (`common/measure.py`'s `run_fresh_session`).
- **First push to a pre-created GitHub repo hits a merge conflict.**
  Creating the GitHub repo via the web UI (with an auto-generated
  placeholder README) before the first push causes a `fetch first`/
  unrelated-histories rejection on `git push`. Already resolved once for
  this project's own repo via `git fetch` + `git merge origin/main
  --allow-unrelated-histories`, keeping the real local README over the
  placeholder — see `decisions.md` ADR-009. Not expected to recur unless a
  new sibling repo is created the same way.

## Environment-Specific Notes

- **Windows** is this project's actual development environment:
  PowerShell command examples throughout `README.md`/`RECIPE.md` use
  backtick line continuations and `\`-style paths.
- `pip install numpy Pillow pythonnet` plus Thorlabs Kinesis (64-bit) and
  the IDS Peak SDK are needed only for real (non-dry-run) hardware use —
  none of the 97 tests or any `--dry-run` session need them.

## When You Hit Something New

Add it here, dated, with what was tried and what actually fixed it.
