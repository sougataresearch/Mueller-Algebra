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
