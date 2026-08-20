# Architecture — MMIE

## High-Level Architecture

```
                    ┌─────────────────────────────┐
                    │ section2_qwp_calibration/     │
                    │ qwp_calibration.py            │   camera is its own null
                    │ (null-search + Hauge Eq 19-21, │   detector -- no external
                    │  Eq 4 closed-form solve)       │   polarimeter needed
                    └──────────────┬───────────────┘
                                   │ Config/calibration_result.json
                                   │ (per-QWP s,f,r,delta_deg,T)
                                   ▼
   ┌───────────────────────────────────────────────────────────────┐
   │                    common/measure.py                           │
   │  (acquisition -- ONE script serves Sections III/IV/V; which    │
   │   --mode/--acquisition you pick decides which section's math   │
   │   reads the output)                                            │
   │        │                          │                            │
   │  motor_communication.py    camera_communication.py              │
   │  (Thorlabs K10CR2/M)       (IDS Peak)                           │
   └────────┬──────────────────────────┬─────────────────────────────┘
            │ Data/<run>/Images, Config, Logs, Results
            ▼                          ▼                          ▼
 section3_discrete_       section4_single_arm_       section5_dual_arm_
 reconstruction/          continuous/                continuous/
 discrete_                continuous_single_arm_     continuous_dual_arm_
 reconstruction.py        reconstruction.py +         reconstruction.py +
 (Hauge Eq 8/11/17/18)    _calibration.py             _calibration.py
                          (Hauge Eq 41-50, 57-61)     (Hauge Eq 62-66, 75-79)
            │                          │                          │
            ▼                          ▼                          ▼
     Results/mueller_matrix.npy + mueller_matrix_summary.json (all three)
```

## Module Breakdown

| Module | Responsibility | Status |
|---|---|---|
| `common/motor_communication.py` | Thorlabs K10CR2/M rotation-stage wrapper (connect, home, move, continuous spin, dry-run simulation) | Working, reused as-is (never modified per section) |
| `common/camera_communication.py` | IDS Peak camera wrapper (open/configure, triggered capture, TIFF save+verify, ROI helpers, dry-run simulation) | Working, reused as-is |
| `common/measure.py` | Acquisition for all of 3×3/3×4/4×3/4×4, discrete and continuous | Working — 35 tests |
| `section2_qwp_calibration/qwp_calibration.py` | Null-search + closed-form QWP retardance calibration | Working — 20 tests |
| `section3_discrete_reconstruction/discrete_reconstruction.py` | Mode-agnostic discrete-mode Mueller matrix solve | Working — 13 tests |
| `section4_single_arm_continuous/continuous_single_arm_reconstruction.py` | Per-revolution + outer-angle Fourier fit, E/B/F reconstruction (3×4/4×3) | Working — 14 tests (shared suite) |
| `section4_single_arm_continuous/continuous_single_arm_calibration.py` | Built-in phase-offset + rotating-QWP-defect calibration, cross-checked against Section II | Working (library function only, no CLI — see `MMIE_ATOMIC_TARGETS.md` Category 6) |
| `section5_dual_arm_continuous/continuous_dual_arm_reconstruction.py` | 25-coefficient Fourier fit + full 16-element inversion (4×4) | Working — 15 tests (shared suite) |
| `section5_dual_arm_continuous/continuous_dual_arm_calibration.py` | Built-in phase-origin + both-QWP-defect calibration, cross-checked against Section II | Working (library function only, no CLI) |

## Data Flow

1. `qwp_calibration.py` writes
   `Data/QWP_Calibration/<date>_<target>_NN/Config/calibration_result.json`
   — both QWPs' `s,f,r,delta_deg,T` (per-pixel `.npy` + ROI summary), used
   by every reconstruction module downstream.
2. `measure.py`, given `--mode`/`--acquisition`, writes
   `Data/<date>_<label>_NN/` with `Images/`, `Config/experiment_config.json`,
   `Logs/` (`experiment_log.csv` for continuous, `measurement_log.csv` for
   discrete), `Results/{Bright,Dark}Reference.tiff`.
3. The matching reconstruction script (`discrete_reconstruction.py` for
   discrete-mode output; `continuous_single_arm_reconstruction.py` for
   3×4/4×3 continuous; `continuous_dual_arm_reconstruction.py` for 4×4
   continuous) reads that run directory plus the Section II calibration
   folder(s), and writes `Results/mueller_matrix.npy` +
   `Results/mueller_matrix_summary.json` back into the same run directory.
4. Sections IV/V's own calibration modules run against a *separate*,
   sample-absent `measure.py` continuous-mode session and report a diff
   against Section II's numbers for the same QWP — a cross-check, not an
   input to the main reconstruction path.

## Component Responsibilities

- **Acquisition never does reconstruction math, and vice versa.**
  `measure.py` only ever writes intensity images/frames and metadata;
  every Mueller-matrix formula lives in the four section-3/4/5 modules.
  `measure.py` is never modified by any reconstruction change (spec Part
  5, `rules.md`).
- **`common/` is not "Section I"** — it's shared infrastructure that
  Sections III, IV, and V all read the output of, which is why it lives
  outside the `sectionN_*` naming rather than under one of them
  (`decisions.md` ADR-002).
- **Each reconstruction module is mode-agnostic within its own scope**:
  `discrete_reconstruction.py` dispatches on `experiment_config.json`'s
  `"mode"` field rather than requiring a separate script per mode;
  `continuous_single_arm_reconstruction.py` is one function parameterized
  by which side rotates (3×4 vs. 4×3), not two duplicated functions.

## External Services

None. Fully local — no network calls, no external database, no external
polarimeter (the camera is its own null detector, Section II's whole
point). All state lives in `Data/` on local disk.

## Technology Choices

- **Pure Python + NumPy** for all reconstruction/calibration math (spec
  Part 5) — no hardware dependency in Sections II–V's math modules, only
  in `common/motor_communication.py`/`camera_communication.py` and
  Section II's acquisition half.
- **Thorlabs Kinesis (pythonnet)** for motor control, **IDS Peak SDK** for
  the camera — both optional at development time; every hardware-touching
  script has a `--dry-run` mode that needs neither installed.
- **Pillow** for TIFF I/O.
- No GPU, no autodiff framework, no database — nothing here has needed
  one; the workload is per-pixel-vectorized NumPy linear algebra on
  single-camera-frame-sized arrays, not a combinatorial batch workload
  (contrast with the sibling `ocd_library` project's much larger-scale
  needs).

## Directory Structure

```
algebra/  (MMIE project root)
├── README.md                        overview, setup, end-to-end pipeline, testing
├── RECIPE.md                        step-by-step run order (cookbook)
├── DRY_RUN_WALKTHROUGH.md           what --dry-run actually does, screen-by-screen
├── MMIE_FULL_PROJECT_SPEC.md        original task spec (Sections II-V scope)
├── PRD.md                           this project's requirements (this file's sibling)
├── architecture.md                  this file
├── design.md                        algorithm-level design decisions, bugs found+fixed
├── rules.md                         coding/AI rules
├── testing.md                       validation strategy per suite
├── decisions.md                     this project's own ADR log
├── memory.md                        living project-status snapshot
├── progress_log.md                  dated discussion/action-item log
├── troubleshooting.md               known gotchas
├── CONVENTIONS.md                   Hauge-notation and file/folder conventions
├── deployment.md                    environment setup (no CI -- single-user project)
├── MMIE_ATOMIC_TARGETS.md           fine-grained target checklist
├── LICENSE                          MIT
├── josa-68-11-1519.pdf              Hauge (1978), primary reference
├── common/
│   ├── motor_communication.py, camera_communication.py, measure.py
│   ├── test_measure.py
│   └── README.md
├── section2_qwp_calibration/
│   ├── qwp_calibration.py, test_qwp_calibration.py
│   └── README.md
├── section3_discrete_reconstruction/
│   ├── discrete_reconstruction.py, test_discrete_reconstruction.py
│   └── README.md
├── section4_single_arm_continuous/
│   ├── continuous_single_arm_reconstruction.py
│   ├── continuous_single_arm_calibration.py
│   ├── test_continuous_single_arm.py
│   └── README.md
└── section5_dual_arm_continuous/
    ├── continuous_dual_arm_reconstruction.py
    ├── continuous_dual_arm_calibration.py
    ├── test_continuous_dual_arm.py
    └── README.md
```

`Data/` and `Results/` (run outputs) are gitignored — generated by
`measure.py`/`qwp_calibration.py` runs, not checked in.

## Scalability Considerations

Not a scaling-sensitive project in the way a batch/combinatorial workload
would be: one bench, one sample at a time, one camera frame's worth of
pixels per reconstruction. The per-pixel math is already fully vectorized
(a `height x width x 4 x 4` NumPy array via one batched `linalg.solve`/
`linalg.lstsq` call, never a per-pixel Python loop) — this is the
project's actual performance requirement, already met, not a future
target.

## Security Considerations

No `eval`/`exec`, no untrusted deserialization, no network-facing surface.
The only external input is camera/motor hardware I/O (or its dry-run
simulation) and locally-authored config constants — nothing here processes
untrusted user input.
