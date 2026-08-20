# Product Requirements Document — MMIE (Mueller Matrix Imaging Ellipsometry)

## Problem Statement

A Mueller matrix `M` fully describes what a sample does to polarized
light (`Stokes_out = M @ Stokes_in`), but recovering it requires driving
known polarization states at a sample and analyzing the output at enough
(generator, analyzer) combinations to solve for all 16 entries. Doing that
correctly requires knowing the *real* retardance/diattenuation of every
quarter-wave plate (QWP) in the beam path — no real QWP is a perfect 90°
retarder, and treating it as one silently biases the recovered matrix.
Hauge (1978), *"Mueller matrix ellipsometry with imperfect compensators"*
(JOSA 68(11), 1519 — `josa-68-11-1519.pdf`), gives the closed-form physics
for both problems: measuring real QWP defects with no external
polarimeter, and reconstructing a sample's Mueller matrix via three
different acquisition strategies. This project implements that paper as a
working bench-control and analysis pipeline.

## Goals

1. Measure the real `s,f,r,delta,T` defect parameters of both QWPs on the
   bench, using the camera itself as a null detector — no external
   polarimeter required (Section II).
2. Reconstruct an unknown sample's Mueller matrix via three acquisition
   strategies, each a genuine alternative rather than a refinement of the
   others:
   - **Discrete** (Section III): a static grid of images, simplest to
     reason about, most flexible mode support (3×3/3×4/4×3/4×4).
   - **Single-arm continuous** (Section IV): one QWP spins continuously,
     the other arm's polarizer stepped — faster than discrete, a
     validation stepping-stone toward Section V.
   - **Dual-arm continuous** (Section V): both QWPs spin together at a
     locked 5:1 ratio — the production path, fastest and most
     overdetermined, recovers all 16 elements with no structural blind
     spots.
3. Give Sections IV and V their own built-in calibration (a separate,
   sample-absent run) that cross-checks against Section II's numbers for
   the same QWP — agreement is confidence in both, disagreement flags
   something to investigate, rather than trusting either blindly.
4. Every reconstruction path must expose the same physical-realizability
   diagnostic (`trace(M^T@M) <= 4*M00^2`) and be validated the same way:
   a synthetic round-trip from a KNOWN Mueller matrix and KNOWN QWP
   defects, through the same forward formulas the module implements, back
   to the known answer.

## Success Criteria

- Every reconstruction/calibration module recovers a known, synthetically-
  generated Mueller matrix to near machine precision in the noiseless
  case, and degrades gracefully (not catastrophically) under added noise
  — verified 2026-08-20: **111 tests across 5 hardware-independent
  suites, all passing** (`common` 35, `section2_qwp_calibration` 27,
  `section3_discrete_reconstruction` 14, `section4_single_arm_continuous`
  17, `section5_dual_arm_continuous` 18).
- A `--dry-run --no-prompt` pass of both acquisition scripts
  (`common/measure.py`, `section2_qwp_calibration/qwp_calibration.py`)
  completes end to end with no hardware installed, exercising every code
  path a real run would (see `DRY_RUN_WALKTHROUGH.md`).
- Every mode's structurally-unrecoverable rows/columns come back as `NaN`
  (not a wrong number) — 3×3/3×4/4×3 never fabricate the sub-block they
  cannot see.
- Two real numerical bugs were found and fixed before being trusted (see
  `decisions.md` ADR-006/ADR-007) — both now have permanent regression
  tests, not just a one-off fix.

## Functional Requirements

- FR1: `qwp_calibration.py` performs an automated (default) or interactive
  null-search to discover the analyzer's and each QWP's true optical zero,
  then solves for `s,f,r,delta_deg,T` via either the exact 3-angle
  closed form or an N-angle least-squares fit, for both QWPs in one
  session.
- FR2: `common/measure.py` acquires images/frames for any of
  3×3/3×4/4×3/4×4, in either discrete or continuous mode, writing a
  self-describing output folder (`Images/`, `Config/`, `Logs/`,
  `Results/`, `Checkpoints/` for discrete) that every reconstruction
  module reads without re-deriving the format.
- FR3: `discrete_reconstruction.py`, `continuous_single_arm_reconstruction.py`,
  and `continuous_dual_arm_reconstruction.py` each turn one `measure.py`
  output folder plus the relevant Section II calibration folder(s) into a
  per-pixel Mueller matrix `.npy` and an ROI-summarized JSON.
- FR4: `continuous_single_arm_calibration.py` and
  `continuous_dual_arm_calibration.py` independently recover their own
  QWP's `s,f,r` from a sample-absent continuous-mode session and report a
  diff against Section II's numbers for the same QWP.

## Non-Functional Requirements

- All per-pixel math vectorized — no per-pixel Python loop anywhere in
  Sections II–V's math.
- Every function's docstring cites the Hauge equation number(s) it
  implements — traceability for a thesis-adjacent tool.
- `motor_communication.py`/`camera_communication.py` reused as-is;
  `measure.py` never modified by any reconstruction module — acquisition
  and reconstruction stay strictly separated (`rules.md`).
- Dry-run support throughout every hardware-touching script, with no
  Kinesis/pythonnet/IDS Peak SDK installed — this is how the project is
  actually developed and tested today (no bench access in this
  environment).

## User Stories

- As the bench operator, I want to calibrate both QWPs once per
  bench/wavelength and reuse that calibration across many samples, so I'm
  not re-deriving QWP defects for every measurement.
- As the bench operator, I want a fast production path (Section V) for
  routine samples, with a slower discrete mode (Section III) available
  when I want the simplest possible mental model of what was measured.
- As a future maintainer, I want every reconstruction formula traceable to
  a specific Hauge equation, so a suspected bug can be checked against the
  paper directly instead of against someone's memory of the derivation.

## Constraints

- Single-user, single-bench project — no team, no CI, matches the
  project's actual current scope.
- No real hardware has been exercised in this development environment —
  everything verified here is dry-run/synthetic; real-bench validation is
  the responsibility of whoever runs this on the actual instrument (see
  `troubleshooting.md`'s Anticipated Gotchas).
- `common/measure.py` is intentionally not owned by any one section
  folder — it serves Sections III, IV, and V simultaneously
  (`common/README.md`, `decisions.md` ADR-002).

## Risks

- **Untested against a real bench**: every synthetic round-trip test
  confirms the *math* is correct given the stated forward model: it
  cannot catch a real-world effect the forward model doesn't include
  (camera nonlinearity, genuine misalignment beyond what dry-run
  simulates). Mitigated by the built-in calibration cross-checks
  (Section IV/V vs. Section II) and the realizability diagnostic, both of
  which are designed to flag exactly this kind of real-world
  disagreement.
- **Underdetermined-fit bugs that don't raise an error**: `numpy.linalg.lstsq`
  silently returns *a* solution for an underdetermined system rather than
  erroring — this already caused two real, found-and-fixed bugs (Section
  IV's outer-angle fit, Section V's below-25-frame fit). Any *new*
  Fourier-fit code added later must be checked against this same failure
  mode before being trusted (`rules.md` AI Coding Rule 2).
- **Aliased angle grids**: evenly-spaced angle grids can alias in
  `cos(2*theta)`/`cos(4*theta)` (Section II's N=3, Section III's
  0/45/90/135 case) — silently giving a rank-deficient system that looks
  fine until the numbers come out wrong. Mitigated by
  `check_angle_grid_rank`/`suggest_angle_grid`, but a genuinely new grid
  choice should still be rank-checked, not assumed safe by inspection.

## Out-of-Scope Items

- A GUI or web interface — command-line/script-driven throughout, matching
  the bench-tool nature of this project.
- Continuous-acquisition resume — `measure.py`'s continuous mode has never
  supported `--resume` (discrete only); an interrupted revolution or outer
  step restarts from scratch, by design (`common/README.md`).
