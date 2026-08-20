# Recipe — what to run, in what order

This is a cookbook: follow it top to bottom the first time. Every command
is given exactly as you'd type it, from the project root (`D:\algebra`),
in PowerShell. Section READMEs (`section2_qwp_calibration/README.md` etc.)
have the physics/math behind each step — this file only has the *order of
operations* and the exact commands.

Read [DRY_RUN_WALKTHROUGH.md](DRY_RUN_WALKTHROUGH.md) alongside this file
the first time — it shows you what each command actually prints and writes
to disk, so you recognize normal output from a real problem.

## Step 0 — one-time machine setup

1. Install Thorlabs Kinesis (64-bit) and the IDS Peak SDK — only needed
   for *real* hardware runs. Everything below works without them via
   `--dry-run`.
2. `pip install numpy Pillow pythonnet`
3. Open `common/measure.py` and check the **USER SETTINGS** block near the
   top: `MOTOR_SERIALS`, `ZERO_OFFSETS_DEG`, `motor_communication.KINESIS_DIR`
   (only matters once you drop `--dry-run`).

```powershell
pip install numpy Pillow pythonnet
```

## Step 1 — run every test suite (no hardware needed)

Do this before touching real equipment, and again any time you edit code.

```powershell
cd common                            ; python -m unittest test_measure -v                      ; cd ..
cd section2_qwp_calibration          ; python -m unittest test_qwp_calibration -v               ; cd ..
cd section3_discrete_reconstruction  ; python -m unittest test_discrete_reconstruction -v        ; cd ..
cd section4_single_arm_continuous    ; python -m unittest test_continuous_single_arm -v          ; cd ..
cd section5_dual_arm_continuous      ; python -m unittest test_continuous_dual_arm -v            ; cd ..
```

All five must pass (`OK` at the bottom of each). If one fails, fix it (or
ask for help) before going further — everything downstream assumes the
math is correct.

## Step 2 — calibrate both QWPs (Section II) — always first, real or dry-run

Every reconstruction script (Steps 3-5) needs to know the *real* defect
parameters (`s,f,r,delta,T`) of whichever QWP is in the beam. Do this once
per bench/wavelength, before any sample measurement:

```powershell
python section2_qwp_calibration\qwp_calibration.py --dry-run --no-prompt --target both
```

This writes
`Data\QWP_Calibration\<date>_PSG_QWPandPSA_QWP_01\Config\calibration_result.json`
— note the exact folder name it prints at the end; you'll pass that path
into every reconstruction command below via `--psg-calibration-dir` /
`--psa-calibration-dir` / `--rotating-calibration-dir`.

Once your setup is validated in dry-run (Steps 1-2 above look sane), drop
`--dry-run` to calibrate for real. Keep `--no-prompt` off for a real run —
you want the operator confirmations (illumination on, physical QWP swap,
etc.) active.

## Step 3 — pick ONE acquisition mode and measure a sample

You only need one of 3a/3b/3c below per sample — pick based on which
section you're validating or which is your production path (3c is
production once everything's checked out). All three read the same
calibration output from Step 2.

### 3a. Discrete mode (Section III) — static grid, slowest, simplest to reason about

```powershell
python common\measure.py --mode 4x4 --acquisition discrete --dry-run --no-prompt --run-label sample1

python section3_discrete_reconstruction\discrete_reconstruction.py "Data\2026-08-18_sample1_01" `
    --psg-calibration-dir "Data\QWP_Calibration\2026-08-18_PSG_QWPandPSA_QWP_01" `
    --psa-calibration-dir "Data\QWP_Calibration\2026-08-18_PSG_QWPandPSA_QWP_01"
```

### 3b. Single-arm continuous (Section IV) — validation stepping-stone

```powershell
python common\measure.py --mode 4x3 --acquisition continuous --dry-run --no-prompt --run-label sample2

python section4_single_arm_continuous\continuous_single_arm_reconstruction.py "Data\2026-08-18_sample2_01" `
    --rotating-calibration-dir "Data\QWP_Calibration\2026-08-18_PSG_QWPandPSA_QWP_01"
```

This section also has a built-in calibration cross-check that compares its
own recovered rotating-QWP `s,f,r` against Step 2's numbers for the same
QWP — run it against a **separate, sample-absent** session whose
`OUTER_ANGLES_DEG` includes `0.0` (the default `[0, 45, 90]` already does):

```powershell
python common\measure.py --mode 4x3 --acquisition continuous --dry-run --no-prompt --run-label calcheck4
python section4_single_arm_continuous\continuous_single_arm_calibration.py "Data\2026-08-18_calcheck4_01" `
    --compare-to "Data\QWP_Calibration\2026-08-18_PSG_QWPandPSA_QWP_01\Config\calibration_result.json"
```

### 3c. Dual-arm continuous (Section V) — production path, run this one once everything else checks out

```powershell
python common\measure.py --mode 4x4 --acquisition continuous --dry-run --no-prompt --run-label sample3

python section5_dual_arm_continuous\continuous_dual_arm_reconstruction.py "Data\2026-08-18_sample3_01" `
    --psg-calibration-dir "Data\QWP_Calibration\2026-08-18_PSG_QWPandPSA_QWP_01" `
    --psa-calibration-dir "Data\QWP_Calibration\2026-08-18_PSG_QWPandPSA_QWP_01"
```

Same caveat as 3b: `continuous_dual_arm_calibration.py`'s cross-check
against Step 2 is a library function only (see
`test_continuous_dual_arm.py`'s cross-check test for the call shape), not
a command-line script you can run directly against a `Data\<run>` folder
yet.

## Step 4 — read the results

Every reconstruction command above writes, inside its `Data\<run>\`
folder:

- `Results\mueller_matrix.npy` — per-pixel `height x width x 4 x 4` array,
  normalized so `M00 = 1`.
- `Results\mueller_matrix_summary.json` — one ROI-averaged 4x4 matrix
  (`--roi X Y W H` on the reconstruction command to pick the region),
  plus the `trace(M^T@M) <= 4*M00^2` physical-realizability diagnostic
  (and, for Section V, the 9 Eq. 66 consistency checks).
- 3x3/3x4/4x3 modes leave structurally-unrecoverable rows/columns as
  `NaN` in both files — expected, see `common/README.md`'s mode table,
  not a bug.

Open `mueller_matrix_summary.json` first — it's small and human-readable.
Load `mueller_matrix.npy` with `numpy.load(...)` for the full per-pixel map.

## Step 5 — going from dry-run to a real experiment

1. Confirm Step 1's tests all pass and a Step 2 `--dry-run --no-prompt`
   pass completes and prints sane-looking numbers (roughly `s≈0`,
   `f≈0.5`, `delta_deg` in the 80-100° range for a real QWP).
2. In `common/measure.py`'s **USER SETTINGS** block, set the real
   `MOTOR_SERIALS`, `ZERO_OFFSETS_DEG`, camera settings for your bench.
3. Re-run Step 2 for real (drop `--dry-run`, keep the operator prompts —
   i.e. don't add `--no-prompt`) to calibrate both QWPs.
4. Re-run whichever of 3a/3b/3c you need, dropping `--dry-run`, with a
   real sample inserted when prompted.
5. See [DRY_RUN_WALKTHROUGH.md](DRY_RUN_WALKTHROUGH.md) for exactly what
   changes between dry-run and real (what's simulated vs. what's real
   hardware I/O), so you know what to expect the first time you flip
   the switch.

## Quick reference — order of operations, one line each

1. Run all 5 test suites.
2. `qwp_calibration.py --target both` (Section II) — once per bench/wavelength.
3. `measure.py` (pick a mode) — once per sample.
4. The matching `*_reconstruction.py` for that mode, pointed at Step 2's
   calibration folder.
5. Read `Results\mueller_matrix_summary.json`.
6. Repeat 3-5 for more samples; repeat 2 only if the bench or wavelength changes.
