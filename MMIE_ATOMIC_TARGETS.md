# MMIE Completion Plan — Atomic Targets

Mirrors the sibling `ocd_library`/`sougata_solver` projects'
atomic-targets format and discipline, applied to this project's own scope
(`MMIE_FULL_PROJECT_SPEC.md`). Unlike those projects at their current
stage, most of this file is **already complete** — this is a mature-
project status checklist, not a forward-looking scope breakdown.

## How to use this register

Each target is small enough to implement, review, and test in one
isolated change. A target is not done until its stated check passes.
Update this file (and `README.md`'s pointers) when a target's status
changes. Correctness/validation precede convenience — real-hardware
validation (Category 7) is explicitly the last unchecked category, not
because it's unimportant but because everything upstream of it must be
right first.

---

## 1. Shared Acquisition Layer (`common/`) — COMPLETE

- [x] **1.1** `motor_communication.py` — Thorlabs K10CR2/M wrapper
  (connect, home, move, continuous spin, dry-run simulation). Reused
  as-is per spec (`decisions.md` ADR-003).
- [x] **1.2** `camera_communication.py` — IDS Peak wrapper (open/
  configure, triggered capture, TIFF save+verify, ROI helpers, dry-run
  simulation). Reused as-is.
- [x] **1.3** `measure.py` — full operator-guided acquisition for
  3×3/3×4/4×3/4×4, discrete and continuous, with automatic bright/dark
  reference verification, retried moves/captures, crash-safe
  checkpointing (discrete), full session transcript, multi-sample
  looping, pre-flight angle-grid rank check.
- [x] **1.4** 35 hardware-independent tests (`common/test_measure.py`),
  covering one full `--dry-run --no-prompt` session per mode/acquisition
  combination.

### Exit criteria — met

`python -m unittest test_measure -v` passes (35/35, verified 2026-08-20);
a `--dry-run --no-prompt` session completes for every mode/acquisition
combination.

---

## 2. Section II — QWP Retardance Calibration — COMPLETE

- [x] **2.1** Automated null-search (coarse grid + golden-section
  refinement) against real ROI-mean camera readings, with an interactive
  fallback.
- [x] **2.2** Closed-form 3-angle solve (Hauge Eq. 19-21, Eq. 4) and
  N-angle least-squares generalization (`--calibration-angle-mode
  least_squares`).
- [x] **2.3** Both QWPs calibrated back-to-back in one session, with an
  operator-confirmed physical swap.
- [x] **2.4** Sanity-check warnings (`abs(s)>0.15`, `abs(f-0.5)>0.15`,
  `delta_deg` outside 80-100°, `p^2+s^2>1` pixel count).
- [x] **2.5** N=3-aliasing guard in `least_squares_calibration_angles()`
  (`design.md` §4).
- [x] **2.6** 20 tests (`section2_qwp_calibration/test_qwp_calibration.py`),
  including the full dry-run session against `DryRunOpticalBench`'s
  hidden ground-truth model.
- [x] **2.7** Cross-check a compensator's achieved null intensity against
  the analyzer-vs-polarizer null's own (`decisions.md` ADR-012) — a
  compensator null much brighter than the bare P/A floor flags a real
  problem (wrong axis, misalignment, dirty optics, stray light), not just
  noise. Both intensities saved in `calibration_result.json`'s
  `null_search` field.

### Exit criteria — met

`python -m unittest test_qwp_calibration -v` passes (25/25, verified
2026-08-20); `Config/calibration_result.json` is written with both QWPs'
`s,f,r,delta_deg,T` and discovered zero-offsets.

---

## 3. Section III — Discrete-Mode Reconstruction — COMPLETE

- [x] **3.1** Mode-agnostic reconstruction (`experiment_config.json`'s
  `"mode"` dispatches to the right generator/analyzer vector
  construction).
- [x] **3.2** Exact-inverse (exactly 4 states/side) and least-squares
  (overdetermined) solve paths.
- [x] **3.3** `suggest_angle_grid()` with the `0/45/90/135` aliasing-trap
  fallback (`design.md` §4).
- [x] **3.4** Realizability diagnostic (`trace(M^T@M) <= 4*M00^2`) on
  every reconstruction.
- [x] **3.5** 13 tests, including the aliasing-trap regression case and
  each mode's exact NaN sub-block confirmation.

### Exit criteria — met

`python -m unittest test_discrete_reconstruction -v` passes (13/13,
verified 2026-08-20); known M recovered to near machine precision
noiseless, both exact-inverse and least-squares paths.

---

## 4. Section IV — Single-Arm Continuous — COMPLETE (CLI cross-check open, see Category 6)

- [x] **4.1** Per-revolution Fourier fit + 4-vector recovery (Hauge Eq.
  41-43).
- [x] **4.2** Outer-angle Fourier fit with the reduced-3-parameter-model
  fix for the no-QWP outer side (`decisions.md` ADR-006).
- [x] **4.3** E/B/F reconstruction, mirror-symmetric for 3×4 vs. 4×3 (one
  function, not duplicated).
- [x] **4.4** Built-in calibration module (`continuous_single_arm_calibration.py`):
  C1′ phase offset, rotating-side s′/f′, outer-side cross-check,
  source-response recovery, `cross_check_against_part1()`.
- [x] **4.5** 14 tests, including the exact `OUTER_ANGLES_DEG=[0,45,90]`
  regression case for target 4.2's bug.

### Exit criteria — met

`python -m unittest test_continuous_single_arm -v` passes (14/14,
verified 2026-08-20); known M recovered to near machine precision at
`measure.py`'s literal default outer-angle config.

---

## 5. Section V — Dual-Arm Continuous (Production Path) — COMPLETE (CLI cross-check open, see Category 6)

- [x] **5.1** 25-coefficient combined Fourier fit against real logged
  `(C, C')` pairs.
- [x] **5.2** Full 16-element closed-form inversion (Hauge Eq. 65).
- [x] **5.3** 9 Eq. 66 consistency-check diagnostics, reported not fed
  back into the inversion.
- [x] **5.4** Below-25-frames `ValueError` guard (`decisions.md`
  ADR-007).
- [x] **5.5** Built-in calibration module
  (`continuous_dual_arm_calibration.py`): C1/C1′ phase origins,
  both-QWP s/f/s′/f′, `cross_check_against_part1()`.
- [x] **5.6** 15 tests, including the below-25-frames regression case and
  the noiseless exact-satisfaction check for all 9 consistency
  relationships.

### Exit criteria — met

`python -m unittest test_continuous_dual_arm -v` passes (15/15, verified
2026-08-20); all 16 elements recovered with no structural `NaN`s.

---

## 6. Calibration Cross-Check CLI (Sections IV/V) — COMPLETE

### Already present

`continuous_single_arm_calibration.run_single_arm_calibration()` /
`continuous_dual_arm_calibration.run_dual_arm_calibration()` and both
modules' `cross_check_against_part1()` — fully implemented and tested
against synthetic data (Categories 4/5 above).

### Delivered

- [x] **6.1** `load_and_run_single_arm_calibration(run_dir)` /
  `load_and_run_dual_arm_calibration(run_dir)` — read a `measure.py`
  sample-absent continuous-mode run directory (reusing
  `load_continuous_single_arm_run`/`load_dual_arm_run`, the same loaders
  the reconstruction CLIs already use for a real sample) and drive the
  full calibration sequence from it. Section IV's phase-reference
  revolution (Eq. 57/58's "fixed-side + outer axis both at optical 0") is
  read directly from that session's own outer=0 step
  (`find_zero_outer_step()`) rather than requiring a second, separately-
  captured revolution — `OUTER_ANGLES_DEG` must include `0.0` (the
  `measure.py` default already does).
- [x] **6.2** `argparse`/`__main__` wrapper for each calibration module:
  a run directory, `--compare-to <calibration_result.json>`,
  `--aggregation {mean,median}`, `--roi X Y W H`. Prints the recovered
  parameters and (with `--compare-to`) the cross-check diff, and writes
  `Results/single_arm_calibration_cross_check.json` /
  `Results/dual_arm_calibration_cross_check.json`.
- [x] **6.3** Two new tests per section (`test_accepts_genuine_per_pixel_intensities`,
  `test_load_and_run_against_real_dry_run_session`) — the latter runs an
  actual `measure.py --dry-run --no-prompt` session (`DATA_ROOT`
  monkeypatched to a temp directory) and feeds its real output through the
  new loader, not just synthetic in-memory arrays.

### Real bugs found and fixed while building this (not present before)

Manually running the new CLI against a real dry-run session (rather than
only the pre-existing synthetic 1-D-array tests) immediately surfaced
three crashes, all the same root cause — a function assumed its per-pixel
inputs would already be scalar, and forced a direct `float()` cast that
raises on a genuine `(H, W)` array:

- `continuous_single_arm_calibration.measure_phase_offset` (C1' is a
  single mechanical/encoder-zero parameter, correctly reduced via
  `nanmedian` — not a per-pixel optical quantity, and it must be scalar
  regardless since it shifts the shared, frame-indexed angle axis
  `fit_revolution_fourier`'s single design matrix depends on).
- `continuous_single_arm_calibration.measure_non_rotating_side_defects`
  (same fix, plus a second bug in the same function: `expected_b`'s
  `(4,5)` shape didn't broadcast against a per-pixel `e_mat`'s `(4,5,H,W)`
  without an explicit reshape — fixed with the same reshape-then-broadcast
  pattern `reconstruct_single_arm`'s own `b_mat` handling already uses).
- `continuous_dual_arm_calibration.solve_phase_origins`'s `phi_prime` (C1,
  C1' are the same kind of single mechanical parameter as Section IV's
  C1').

See `decisions.md` ADR-011 for the full account. This is exactly the
value target 6.3 was scoped to catch — the synthetic-array tests alone
never exercised a genuine multi-pixel intensity shape.

### Exit criteria — met

Running the new CLI against a real `--dry-run --no-prompt` sample-absent
session directory (verified 2026-08-20) produces the same cross-check
report shape the library functions already produce with synthetic data;
both sections' full suites pass (16/16, 17/17) including the new
regression and integration tests.

---

## 7. Real-Hardware Validation — NOT STARTED

### Already present

Every synthetic round-trip test (Categories 1-5) and a `--dry-run`
session that exercises the full operator workflow with no hardware
installed.

### Current scope

Nothing in this project's actual development environment has touched a
real bench — no Kinesis, no IDS Peak SDK installed here. This category is
inherently something only the bench operator, on the real instrument, can
complete.

### Small targets

- [ ] **7.1** Environment checklist complete on the real bench machine
  (Kinesis + IDS Peak SDK installed, `check_environment()` reports all
  `OK`) — see `deployment.md`.
- [ ] **7.2** Section II calibration run for real, both QWPs, numbers
  sane (`delta_deg` 80-100°, no sanity-check warnings) — see
  `RECIPE.md` Step 2/5.
- [ ] **7.3** One real sample measured via each of Sections III/IV/V,
  Section IV/V's built-in calibration cross-check against Section II
  checked for agreement (`troubleshooting.md`'s Anticipated Gotchas).
- [ ] **7.4** A real, independently-known sample (e.g. a calibrated
  retarder or polarizer of known Mueller matrix) measured and compared
  against its known matrix — the real-hardware equivalent of the
  synthetic round-trip gate (`rules.md` Testing Requirements), since
  synthetic tests alone cannot validate real-world effects the forward
  model doesn't include.

### Exit criteria

A real, independently-known sample's Mueller matrix is recovered within a
stated, measured tolerance — the actual "this works for real," not just
"the math is internally consistent."

---

## 8. Documentation — COMPLETE

- [x] **8.1** Per-section `README.md` (physics, math, I/O schema, run/test
  commands).
- [x] **8.2** Top-level `README.md` (overview, end-to-end pipeline,
  testing, setup checklist).
- [x] **8.3** `RECIPE.md` (step-by-step cookbook), `DRY_RUN_WALKTHROUGH.md`
  (screen-by-screen dry-run account).
- [x] **8.4** `PRD.md`, `architecture.md`, `design.md`, `rules.md`,
  `testing.md`, `troubleshooting.md`, `CONVENTIONS.md`, `deployment.md`,
  `decisions.md`, `memory.md`, `progress_log.md`, this file (added
  2026-08-20).
- [x] **8.5** `LICENSE` (MIT), public GitHub repo.

---

## Status summary

Categories 1-6 and 8: **COMPLETE** (Category 6 completed 2026-08-20 — see
its section above for the three real per-pixel bugs found and fixed along
the way, `decisions.md` ADR-011). Category 7 (real-hardware validation):
**NOT STARTED**, and cannot be completed in this development environment
— the single biggest gap between "the tests pass" and "this is known to
work on a real bench" (`PRD.md`'s Risks, `memory.md`).
