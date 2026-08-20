# Architecture Decision Record — MMIE

## ADR-001: One folder per Hauge paper section, not by acquisition-vs-reconstruction

- **Decision**: the project is organized `section2_qwp_calibration/`,
  `section3_discrete_reconstruction/`, `section4_single_arm_continuous/`,
  `section5_dual_arm_continuous/`, plus a shared `common/` — not split
  along an "acquisition/" vs. "reconstruction/" axis.
- **Why**: `MMIE_FULL_PROJECT_SPEC.md`'s explicit organizing principle —
  each folder maps to one section of Hauge (1978), so a reader can open
  just the piece they're trying to understand instead of a flat pile of
  files (`README.md`).
- **Consequence**: within a section folder, acquisition-adjacent code
  (Section II's null-search) and reconstruction-math code (Section II's
  closed-form solve) sit together, since Hauge's own Section II covers
  both — the paper's structure wins over a code-architecture-first split.

## ADR-002: `common/measure.py` is not owned by any one section folder

- **Decision**: the shared acquisition script lives in `common/`, not
  nested inside `section3_discrete_reconstruction/` (or duplicated across
  III/IV/V).
- **Why**: it's one script that serves Sections III, IV, *and* V
  simultaneously — which `--mode`/`--acquisition` you pick decides which
  section's math reads its output, so it doesn't structurally belong to
  any single one of them (`common/README.md`'s own explicit reasoning).
- **Alternative considered and rejected**: duplicating a thin acquisition
  wrapper per section. Rejected — `measure.py` already handles every mode
  and acquisition type in one place; duplicating it would mean three
  copies of the same operator-workflow logic (environment check, home,
  bright/dark reference, retries, transcript logging) to keep in sync.

## ADR-003: Reuse `motor_communication.py`/`camera_communication.py` as-is

- **Decision**: neither hardware wrapper was rewritten while building
  Sections II–V's new math.
- **Why**: `MMIE_FULL_PROJECT_SPEC.md`'s explicit instruction — both were
  already correct thin wrappers (Thorlabs K10CR2/M, IDS Peak) with
  working dry-run simulation; the actual gaps were in Section II's
  null-search and Sections III/IV/V's reconstruction math, not in the
  hardware layer.

## ADR-004: Pure Python/NumPy for all reconstruction/calibration math

- **Decision**: no hardware dependency anywhere in Sections II–V's math
  modules — only in `common/motor_communication.py`/`camera_communication.py`
  and Section II's acquisition half.
- **Why**: `MMIE_FULL_PROJECT_SPEC.md` Part 5's explicit cross-cutting
  requirement. Consequence: every reconstruction/calibration module's
  correctness is verifiable with a synthetic round-trip test, with no
  bench, motors, or camera required (`testing.md`).

## ADR-005: Synthetic round-trip as the primary correctness gate

- **Decision**: for Sections II–V, generate "measured" intensities from a
  KNOWN Mueller matrix and KNOWN QWP defects using the same forward
  formulas the module implements, feed through reconstruction, confirm
  the known answer returns to near machine precision (noiseless) and
  degrades gracefully under noise — required before considering any
  module done.
- **Why**: `MMIE_FULL_PROJECT_SPEC.md` Part 5's stated primary correctness
  gate. This is what actually caught ADR-006/ADR-007 below — a script
  that runs without raising is not the same claim as one that recovers
  the right answer, and the round-trip test is what tells them apart.

## ADR-006: Section IV's outer-angle fit uses a reduced 3-parameter model when the outer side has no QWP

- **Decision**: `fit_outer_fourier` fits a 3-parameter model
  (`1, cos(2A), sin(2A)`) instead of the full 5-parameter model whenever
  the outer (non-rotating) side is a plain polarizer — always true for
  3×4/4×3.
- **Why**: found directly via the round-trip test (ADR-005), not assumed.
  The full 5-parameter model against `measure.py`'s own
  `OUTER_ANGLES_DEG` default (`[0, 45, 90]` — only 3 angles) is an
  underdetermined system that `numpy.linalg.lstsq` doesn't detect —
  confirmed max Mueller-matrix error of 0.59 instead of machine precision
  before the fix. The reduced model is analytically exact (a plain
  polarizer's vector formula has zero `cos(4A)`/`sin(4A)` dependence), not
  an approximation, and needs only 3 outer angles.
- **Full account**: `design.md` §2, `troubleshooting.md`, permanent
  regression test in `test_continuous_single_arm.py`
  (`test_measure_py_default_three_outer_angles_regression`).

## ADR-007: Section V's dual-arm fit requires ≥25 frames, enforced with a hard error

- **Decision**: `fit_dual_arm_fourier` raises `ValueError` if given fewer
  than 25 frames, rather than letting `numpy.linalg.lstsq` silently return
  a wrong answer for the underdetermined 25-coefficient fit.
- **Why**: same bug class as ADR-006, found the same way — confirmed max
  error of 1.48 (12 frames, from an unusually coarse
  `CAPTURE_ANGLE_STEP_DEG` of 30°) instead of machine precision before
  the fix. Under `measure.py`'s own 1°-default step (360 frames/
  revolution) this guard is nowhere close to being hit — it exists for
  the unusual-config or truncated-acquisition case.
- **Full account**: `design.md` §3, `test_continuous_dual_arm.py`
  (`test_rejects_underdetermined_fit_below_25_frames`).

## ADR-008: `RECIPE.md`/`DRY_RUN_WALKTHROUGH.md` as separate files, not folded into `README.md`

- **Decision** (2026-08-18): added as their own top-level files rather
  than expanding `README.md`'s existing "Running the whole pipeline"
  section in place.
- **Why**: requested directly (step-by-step cookbook + a dry-run
  screen-by-screen walkthrough are both substantial enough documents that
  folding them into `README.md` would bury its existing overview/setup/
  architecture content under procedural detail a first-time reader
  doesn't need immediately.

## ADR-009: First push to the GitHub repo hit a merge conflict against a placeholder README

- **Decision**: resolved via `git fetch` + `git merge origin/main
  --allow-unrelated-histories`, keeping the real local `README.md` over
  GitHub's auto-generated placeholder — not a force-push.
- **Why**: the GitHub repo (`sougataresearch/Mueller-Algebra`) had been
  created via the web UI with an auto-generated README before this
  project's first push, causing a `fetch first`/unrelated-histories
  rejection. A merge (not `--force`) was chosen specifically to avoid
  discarding the remote's own (trivial, but real) commit history.
- **Note**: the sibling `ocd_library` project hit the exact same situation
  independently and resolved it the same way (`ocd_library/troubleshooting.md`)
  — worth remembering as a recurring pattern for any *new* sibling repo
  created the same way, not just a one-off fix here.

## ADR-010: MIT license

- **Decision**: `LICENSE` (MIT) added to the repository, chosen from
  MIT / Apache 2.0 / GPL-3.0 / none, via explicit `AskUserQuestion`.
- **Why**: most permissive common default for research/tool code, and the
  project owner's stated preference when asked directly. No patent-grant
  concern was raised that would favor Apache 2.0, and no copyleft
  requirement was stated that would favor GPL-3.0.

## ADR-011: Calibration cross-check CLI, and a scalar-vs-per-pixel discipline for mechanical parameters

- **Decision**: `continuous_single_arm_calibration.py`/
  `continuous_dual_arm_calibration.py` (Section IV/V's built-in
  calibration cross-check) gained `load_and_run_*_calibration(run_dir)` +
  an `argparse` CLI, reusing the reconstruction modules' own loaders
  (`load_continuous_single_arm_run`/`load_dual_arm_run`). Section IV's
  phase-reference revolution is read from the session's own outer=0 step
  (`find_zero_outer_step()`) rather than requiring a second, dedicated
  capture — justified because Eq. 57/58's reference configuration (fixed-
  side + outer axis both at optical 0) *is* the outer=0 step, not merely
  similar to it.
- **A second decision, forced by testing the first**: `C1'` (Section IV)
  and `C1, C1'` (Section V) are always reduced to a single scalar
  (`np.nanmedian`) even when computed from genuine per-pixel `(H, W)`
  intensities, rather than left as per-pixel maps like `s,f,r,delta_deg,T`
  are. **Why**: these are properties of the rotating stage's own
  mechanical/encoder zero, not spatially-varying optical quantities — and
  they must be scalar regardless, since they shift the shared, frame-
  indexed angle axis that `fit_revolution_fourier`'s single design matrix
  depends on (a per-pixel phase correction would require a per-pixel
  design matrix, defeating that function's whole vectorization scheme).
- **Found only by testing against a real dry-run session, not the
  pre-existing synthetic-array tests**: manually running the new CLI
  against an actual `measure.py --dry-run` session (real `(H, W)` camera
  frames) immediately crashed three functions that had only ever been
  exercised with 1-D scalar-per-frame synthetic arrays:
  `measure_phase_offset`, `measure_non_rotating_side_defects` (which had a
  *second*, independent bug: `expected_b`'s `(4,5)` shape not broadcasting
  against a per-pixel `e_mat`'s `(4,5,H,W)`), and `solve_phase_origins`'
  `phi_prime`. All three were fixed and given permanent per-pixel
  regression tests (`test_accepts_genuine_per_pixel_intensities` in both
  sections' test files) plus a new integration test
  (`test_load_and_run_against_real_dry_run_session`) that runs a real
  `measure.py --dry-run --no-prompt` session (`DATA_ROOT` monkeypatched to
  a temp directory) and feeds it through the new loader — this is what
  actually caught the bugs, and is now a permanent regression guard
  against the same class recurring.
- **Consequence for `rules.md`**: this is the same underlying lesson as
  ADR-006/ADR-007 (an assumption about input shape that only the *actual*
  data, not a hand-written synthetic array, exposed as wrong) — added as
  its own explicit caution rather than folded silently into the existing
  "never trust `lstsq` on an unchecked system size" rule, since the
  failure mode here (a premature scalar cast, not an underdetermined fit)
  is genuinely different.

## ADR-012: Cross-check a compensator's null intensity against the analyzer-vs-polarizer null's

- **Decision**: `search_null_automated`/`search_null_interactive`/
  `run_one_null_search` now return `(angle, achieved_intensity)` instead
  of just the angle. `run_calibration` compares each compensator's null
  intensity against the analyzer-vs-polarizer null's own (found once,
  shared across both QWPs), via a new `null_intensity_mismatch_warning()`,
  folded into the same `sanity_warnings` list/print path as the existing
  `abs(s)`/`abs(f-0.5)`/`delta_deg` checks. Both raw intensities are also
  saved in `calibration_result.json`'s per-target `null_search` field.
- **Why**: proposed directly by the project owner, with sound physical
  reasoning confirmed against the code — crossed P/A alone sets a hard
  intensity floor (that pair's own extinction ratio, dark counts, stray
  light); a properly-aligned compensator between them can only add to
  that floor (extra glass surfaces, its own residual diattenuation), never
  read darker. A compensator null much brighter than the bare P/A null is
  therefore a real signal (wrong axis, misalignment, dirty optics, stray
  light), not just noise — and until this change, nothing checked for it:
  `search_null_automated` computed the achieved intensity, printed it, and
  discarded it, never comparing it to anything.
- **Threshold**: two-part (`SANITY_NULL_INTENSITY_RATIO_MAX = 1.5` AND
  `SANITY_NULL_INTENSITY_ABS_MARGIN = 5.0` counts, both must trip) rather
  than a ratio alone, so two readings already sitting near the camera's
  dark-count floor don't get flagged over noise-level differences that
  would satisfy a bare ratio check. Not from Hauge — an engineering
  addition, documented as such in the code (`rules.md` AI Coding Rule 1's
  spirit: don't attribute a project-added check to the paper).
- **A related, deliberately NOT-adopted idea**: also validating that the
  found null generalizes beyond the one flat ROI used to search it (e.g.
  cross-checking against other regions of the frame). Discussed with the
  project owner and left out for now — the current single-flat-ROI
  approach (`camera_communication.select_roi`, chosen specifically to
  avoid a non-uniform illumination profile biasing the search) was judged
  sufficient; revisit only if a real session's per-pixel retardance maps
  show spatial structure that a single global zero-offset can't explain.
