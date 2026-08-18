# Task: Full MMIE Project — QWP Calibration + Mueller Matrix Reconstruction (Sections II-V)

## 0. Context and what's attached

Standalone project, new folder, nothing else exists. Attached reference
files (use them, do not rewrite from scratch where they already work):

- `motor_communication.py` — thin Thorlabs K10CR2/M wrapper (connect, home,
  point-to-point move, continuous spin, dry-run simulation). **Reuse as-is.**
- `camera_communication.py` — thin IDS Peak camera wrapper (open/configure,
  triggered capture, TIFF save+verify, ROI helpers, dry-run simulation).
  **Reuse as-is.**
- `measure.py` — the lab's working acquisition script. Covers 3x3/3x4/4x3/4x4
  **discrete**-angle acquisition and 3x4/4x3/4x4 **continuous**-rotation
  acquisition. Full operator-guided workflow: environment check -> connect
  only the needed motor axes -> home -> set velocity -> open+configure
  camera -> automatic bright/dark reference capture+verification (operator
  confirm-to-continue if suspicious) -> operator prompt to insert sample ->
  acquisition loop (retried moves, retried captures, crash-safe checkpoint
  for discrete) -> full session transcript logged to disk -> loop for
  multiple samples. **Reuse as-is — this already IS the acquisition code
  needed for Sections III, IV, and V below.** Do not rewrite it.
- `README.md` — documents `measure.py`'s exact output file/folder schema
  (Images/, Config/experiment_config.json, Logs/, Results/) — this is the
  format your reconstruction code (Sections III/IV/V below) must read.
- `qwp_calibration.py` — **draft, incomplete**. Implements the closed-form
  Section II math correctly but has **no null-search** (assumes hardware
  zero-offsets are already correct rather than finding/verifying them) and
  is missing several other pieces — full gap list in Part 1 below.

## Key division of labor (read this before starting)

| Section | Acquisition (capturing images) | Math (turning images into numbers) |
|---|---|---|
| II — QWP retardance | **Build new** — `qwp_calibration.py` needs a null-search added (Part 1) | Already correct in the draft — keep |
| III — discrete Mueller matrix | **Already done** — reuse `measure.py` unmodified | **Build new** — does not exist anywhere (Part 2) |
| IV — single-arm continuous | **Already done** — reuse `measure.py` unmodified | **Build new** — does not exist anywhere (Part 3) |
| V — dual-arm continuous (5:1) | **Already done** — reuse `measure.py` unmodified | **Build new** — does not exist anywhere (Part 4) |

So: one acquisition task (finish Section II's script), three reconstruction-
math tasks (Sections III, IV, V) that all read `measure.py`'s already-fixed
output format.

---

## Part 1 — Section II: QWP retardance calibration (acquisition + math)

### Physics (Hauge 1978, Section II.B)

A real QWP at azimuth `theta` has Mueller matrix (full derivation not
repeated here — implement exactly):
```
C(theta) = [[1, s*cos(2t), s*sin(2t), 0],
            [s*cos(2t), f*cos(4t)+(1-f), f*sin(4t), -r*sin(2t)],
            [s*sin(2t), f*sin(4t), -f*cos(4t)+(1-f), r*cos(2t)],
            [0, r*sin(2t), -r*cos(2t), 1-2f]]
```
with `p=1-2f`, constraint `p^2+r^2+s^2=1`. Ideal QWP: s=0,f=0.5,r=1,delta=90.

**Step 0 — null-calibration (the missing piece)**: with arms collinear, all
optics removed, P defined as 0 deg. Insert analyzer A; **search for the
intensity MINIMUM** by rotating A while reading live camera/ROI intensity —
that angle defines A=90 deg. Insert the compensator under test between the
still-crossed P/A; **search for the null to be RESTORED** by rotating the
compensator — that angle defines C=0 deg for that compensator. This is a
genuine minimum-finding search, not a fixed move. Implement:
- An **interactive mode**: loop of (print current angle + current ROI-mean
  intensity, prompt operator for a relative nudge in degrees or "done",
  move, capture, repeat).
- An **automated mode**: coarse grid search (e.g. every 5 deg across a full
  rotation) to bracket the minimum, then bisection/golden-section refinement
  to a target tolerance (e.g. 0.05 deg), each step driven by real ROI-mean
  camera readings. Provide this as the default with the interactive mode as
  a fallback/manual-override option.
- Persist discovered zero-offsets to `Config/discovered_zero_offsets.json`,
  print a diff against whatever `ZERO_OFFSETS_DEG` value was previously
  assumed.
- Full sequence per Hauge: null A vs fixed P -> insert QWP under test, null
  it against the still-crossed P/A -> operator-confirmed manual step:
  physically remove the OTHER QWP from the beam -> proceed to Step 1 below.

**Step 1-4 — three-angle measurement and closed-form solve** (already
correctly implemented in the attached draft `qwp_calibration.py` — keep):
```
R1,R2,R3 = intensity at C=0,45,90 deg (P=A=0, other QWP physically removed)
s = (R1-R3)/(R1+R3)
f = (R1-2*R2+R3)/(R1+R3)
p = 1-2f
r = sqrt(max(0, 1-p**2-s**2))
delta_deg = degrees(atan2(r, p))     # atan2, not atan(r/p) -- avoid quadrant ambiguity
T = tan(0.5*arccos(clip(s,-1,1)))
```
Run per-pixel (vectorized), plus an ROI median/mean summary. Sanity-check
warnings: `abs(s)>0.15` or `abs(f-0.5)>0.15` (bad null, not bad QWP);
`delta_deg` outside e.g. 80-100 deg; any pixel with `p^2+s^2>1` (clip,
report count).

### Deliverable for Part 1

Extend `qwp_calibration.py` (or split cleanly) to: run the Step-0 null-
search for both A-vs-P and C-vs-(P,A) automatically, in the same session
run both QWPs back-to-back (operator swaps which one is physically removed,
confirmed via prompt) rather than requiring two separate script runs, add
the sanity-check warnings, and save a combined `Config/calibration_result.json`
with both QWPs' `s,f,p,r,delta_deg,T` (summary + per-pixel `.npy` maps) plus
the discovered zero-offsets. Match `measure.py`'s session shape: environment
check, connect/home only needed axes, camera open+configure, transcript
logging, dry-run support throughout (the dry-run camera needs a synthetic
"true null exists somewhere" model so the automated search has something
real to converge on, not just always trivially succeed).

---

## Part 2 — Section III: discrete-mode Mueller matrix reconstruction (math only — acquisition is `measure.py`, already done)

### Input (already produced by `measure.py`, per `README.md`)

`Data/<run>/Images/<first>_<second>.tiff`, `Config/experiment_config.json`
(mode, fixed_angles, state_inputs), `Results/DarkReference.tiff`. Mode table
(first/second axis per mode) is in `README.md` — read it from there, do not
hardcode a different convention.

### Physics (Hauge Section III, generalized beyond Hauge's own worked
examples III.A/III.C to an arbitrary angle grid — see full derivation
already discussed; implement the general form directly)

Generator vector (PSG side, azimuths P,C) and analyzer vector (PSA side,
azimuths C',A), using the REAL measured s,f,r from Part 1's calibration
output (per-pixel maps preferred over scalar summary):
```
S0 = 1 + s*cos(2C-2P)
S1 = f*cos(4C-2P) + s*cos(2C) + (1-f)*cos(2P)
S2 = f*sin(4C-2P) + s*sin(2C) + (1-f)*sin(2P)
S3 = r*sin(2C-2P)
```
(analyzer side is the mathematical dual, with a sign flip on the r' term —
see the r'/-sin term in the analogous d3 expression). For 3x3/4x3 modes
(no QWP on PSG) or 3x3/3x4 modes (no QWP on PSA), that side collapses to
the plain polarizer column/row `{1,cos(2X),sin(2X),0}`.

Forward model: `R = (g*I_P/2) * D @ M @ G`, G's columns = generator vectors
at each commanded (P,C), D's rows = analyzer vectors at each commanded
(C',A). Solve:
- Exactly 4 states/side: `M = (2/(g*I_P)) * inv(D) @ R @ inv(G)`
- Overdetermined (typical — your grids have more than 4 states): solve via
  `numpy.linalg.lstsq` on the vectorized system
  `vec(R) = (g*I_P/2) * kron(G^T, D) @ vec(M)` (preferred over separately
  pseudo-inverting each side — more numerically robust).
- Normalize final M by its own M00 (the epsilon=0/delta=90 reduction always
  gives 2*identity unnormalized, not identity).

Run per pixel (vectorized across the full frame, not a Python loop).

### Deliverable for Part 2

A `discrete_reconstruction` module: mode-agnostic (reads
`experiment_config.json`'s `"mode"` field, dispatches to the right
generator/analyzer vector construction per the mode table), least-squares
by default, exact-inverse as a special case when exactly 4 states/side.
Outputs per-pixel M as a saved `.npy` (`height x width x 4 x 4`) plus an
ROI-summarized single M matrix (JSON). Include the physical-realizability
check (`trace(M^T @ M) <= 4*M00^2`) as a diagnostic on every reconstruction.

---

## Part 3 — Section IV: single-arm continuous reconstruction + calibration (math only — acquisition is `measure.py`'s 3x4/4x3 continuous mode, already done)

### Input

`measure.py`'s continuous-mode output: `Images/frame_NNNN_*.tiff`,
`Logs/experiment_log.csv` (per-frame outer-axis angle + rotating-axis
angle), `Config/experiment_config.json` (`outer_angles`).

### Reconstruction math

Per-revolution Fourier fit (least-squares against the actual logged angles,
not an assumed uniform grid) of the rotating side's intensity to:
```
R(C') = A0 + A2*cos(2C') + B2*sin(2C') + A4*cos(4C') + B4*sin(4C')
```
Recover relative output Stokes vector at that outer-axis step:
```
S0' = (2/(g*I_P)) * [A0 - A4*(1-f')/f']
S1' = (2/(g*I_P)) * A4/f'
S2' = (2/(g*I_P)) * B4/f'
S3' = (2/(g*I_P)) * [-B2 + B4*(s'/f')] / r'
```
Repeat across all outer-axis steps, second Fourier pass (fit each of
S0'..S3' vs. outer angle to the same 5-basis-function model) -> 4x5 matrix
E. Build known 4x5 matrix B from the NON-rotating side's own measured s,f,r
(same generator/analyzer formula as Part 2, expanded into its 5 harmonic
coefficients symbolically). `F = B^T @ inv(B @ B^T)`. `M = E @ F`. Per pixel.

Implement as ONE function parameterized by "which side rotates" (3x4:
PSG_QWP rotates; 4x3: PSA_QWP rotates) — mirror-symmetric, do not duplicate.

### Built-in calibration (Hauge Section IV.C — a SEPARATE calibration run,
same continuous-mode acquisition, system/sample absent)

1. **Phase offset C1'**: sweep the rotating compensator at `P=C=0` (or the
   fixed-side equivalent), no sample. Theory requires B2=B4=0 exactly in
   this configuration. From the raw (phase-uncorrected, "primed") Fourier
   coefficients: `tan(4*C1') = -B4'/A4'`. Solve once, or physically zero the
   compensator's mount so C1'=0 and skip the correction.
2. **Rotating side's own s',f'**: with phase-corrected coefficients from
   step 1: `s' = A2/(A0+A4)`, `f' = 2*A4/(A0+A4)`.
3. **Non-rotating side's s,f — cross-check against Part 1's Section II
   values**: with system absent, theory requires `E = B` exactly (Part 2's
   known matrix). Read off THREE independent estimates each:
   `s = E[0,2] = E[1,2] = F[2,2]`, `f = E[1,4] = F[2,4] = 1-E[1,0]`
   (indices refer to the harmonic-coefficient positions in E, matching
   B's own column layout — const/cos2/sin2/cos4/sin4). Report all three
   estimates and their spread as a genuine internal consistency check, not
   just a single number.
4. **Source response**: `g*I_P = 2*(mean(A0) - mean(A4)*(1-f')/f')`,
   averaged over the outer-axis sweep.

### Deliverable for Part 3

A `continuous_single_arm_reconstruction` module (measurement, Part 3's
first half) and a `continuous_single_arm_calibration` module (steps 1-4
above), both parameterized by which side rotates. Calibration module
outputs should be directly comparable (same JSON schema) to Part 1's
Section II output, to support an explicit cross-check report (agreement =
confidence in calibration; disagreement = flag for investigation).

---

## Part 4 — Section V: dual-arm continuous reconstruction + calibration (math only — acquisition is `measure.py`'s 4x4 continuous mode at your `ROTATION_RATIO`, already done)

### Input

`measure.py`'s 4x4 continuous output: per-frame logged (C, C') angle pairs
in `experiment_log.csv`, `rotation_ratio` (e.g. `[1,5]`) in config.

### Reconstruction math — 25-coefficient Fourier fit

With the 5:1 lock `C' - C1' = 5*(C - C1)`, fit the recorded intensities
(against actual logged angles) to the 25-coefficient model (a0 plus 12
harmonics `theta_1..theta_12`, each with cos and sin amplitude a_j,b_j):
```
theta_1=2C, theta_2=4C, theta_3=2C'-4C, theta_4=2C'-2C, theta_5=2C',
theta_6=2C'+2C, theta_7=2C'+4C, theta_8=4C'-4C, theta_9=4C'-2C,
theta_10=4C', theta_11=4C'+2C, theta_12=4C'+4C
```

Coefficient-to-Mueller-element forward relations (implement exactly):
```
a0 = M00+(1-f)*M01+(1-f')*M10+(1-f)*(1-f')*M11
a1 = s*[(M00+M01)+(1-f')*(M10+M11)]
b1 = s*[M02+(1-f')*M12]+r*[M03+(1-f')*M13]
a2 = f*[M01+(1-f')*M11]
b2 = f*[M02+(1-f')*M12]
a3 = f*[s'*(M01+M11-M22)+r'*M32]/2
b3 = -f*[s'*(M02+M12-M21)+r'*M31]/2
a4 = [s*s'*(M00+M01+M10+M11+M22)+r*s'*M23-r'*s*M32-r*r'*M33]/2
b4 = [s*s'*(M20+M21-M12-M02)-r*s'*(M13+M03)-r'*s*(M30+M31)]/2
a5 = s'*[(M00+M10)+(1-f)*(M01+M11)]
b5 = s'*[M20+(1-f)*M21]-r'*[M30+(1-f)*M31]
a6 = [s*s'*(M00+M01+M10+M11-M22)-r*s'*M23+r'*s*M32+r*r'*M33]/2
b6 = [s*s'*(M20+M21+M12+M02)+r*s'*(M13+M03)-r'*s*(M30+M31)]/2
a7 = f*[s'*(M01+M11-M22)+r'*M32]/2
b7 = f*[s'*(M02+M12+M21)-r'*M31]/2
a8 = f*f'*(M11+M22)/2
b8 = f*f'*(M21-M12)/2
a9 = f'*[s*(M10+M11+M22)+r*M23]/2
b9 = f'*[s*(M20-M12+M21)-r*M13]/2
a10 = f'*[M10+(1-f)*M11]
b10 = f'*[M20+(1-f)*M21]
a11 = f'*[s*(M10+M11-M22)-r*M23]/2
b11 = f'*[s*(M20+M12+M21)+r*M13]/2
a12 = f*f'*(M11-M22)/2
b12 = f*f'*(M21+M12)/2
```
Inversion (25 fitted coefficients -> 16 M elements):
```
M00 = a0 - a2*(1-f)/f - a10*(1-f')/f' + (a8+a12)*(1-f)*(1-f')/(f*f')
M11 = (a8+a12)/(f*f');  M22 = (a8-a12)/(f*f')
M12 = -(b8-b12)/(f*f'); M21 = (b8+b12)/(f*f')
M01 = [a2-(a8+a12)*(1-f')]/f
M02 = [b2+(b8-b12)*(1-f')]/f
M10 = [a10-(a8+a12)*(1-f)]/f'
M20 = [b10-(b8+b12)*(1-f)]/f'
M13 = [(b11-b9)/f' - s*M12]/r
M23 = [(a9-a11)/f' - s*M22]/r
M32 = [(a7-a3)/f + s'*M22]/r'
M31 = [-(b7+b3)/f + s'*M21]/r'
M30 = [-b5+(b7+b3)*(1-f)/f + s'*M20]/r'
M03 = [b1+(b9-b11)*(1-f')/f' - s*M02]/r
M33 = [(a6-a4)+s'*r*M23-s*r'*M32+s*s'*M22]/(r*r')
```
Consistency-check diagnostics (report, do not use in the inversion):
`a1~=b4~=a5~=b6~=0`; `a7+a3~=f*s'*(M10+M11)`; `b7-b3~=f*s'*(M02+M12)`;
`a9+a11~=f'*s*(M10+M11)`; `b9+b11~=f'*s*(M20+M21)`;
`a6+a4~=s*s'*(M00+M01+M10+M11)`. Run all of this per pixel.

### Built-in calibration (Hauge Section V.B — separate run, system absent)

With M=I, only even coefficients up to the 10th survive with appreciable
size (theory): `a0~1.25, a2~0.25*f(1-f'), a4~-0.5, a6~0.5, a8~0.25*f*f',
a10~0.25*f'(1-f)`, and **all b_j=0** (a strong built-in sanity check — real
air data with large b_j indicates misalignment). Use the largest harmonics
(4th, 6th) to solve for the unknown phase origins:
```
C1  = (phi6 - phi4)/4
C1' = (phi6 + phi4)/4
```
with consistency checks `phi4=(phi10-phi2)/2`, `phi6=(phi10+phi2)/2`,
`phi8=phi10-phi2`. Then defect parameters and source response:
```
g*I_P = 2*(A8+A10)*(A8+A2)/A8
f  = A8/(A8+A10)
f' = A8/(A8+A2)
s  = (A1+A9)/(g*I_P)
s' = (A3+A5)/(g*I_P)
```
(r, r' follow from the p^2+r^2+s^2=1 identity, same as Part 1.)

### Deliverable for Part 4

A `continuous_dual_arm_reconstruction` module (25-coefficient fit +
inversion above) and a `continuous_dual_arm_calibration` module (the
phase/defect-parameter recovery above), both operating per-pixel. Same
cross-check philosophy as Part 3: compare recovered s,f,s',f' against
Part 1's Section II values and report agreement/disagreement. **This is
the production path — prioritize correctness and test coverage here over
Parts 2-3.**

---

## Part 5 — Cross-cutting requirements

- Pure Python/NumPy for all reconstruction/calibration math (no hardware
  dependency) — only Part 1's acquisition extension touches
  `motor_communication.py`/`camera_communication.py`.
- All per-pixel math vectorized (no per-pixel Python loop).
- **Unit tests via synthetic round-trip**: for Parts 2-4, generate synthetic
  "measured" intensities from a KNOWN M and KNOWN s,f,r using the same
  forward formulas given above, feed through the reconstruction, confirm
  the known M is recovered to near machine precision (noiseless case) and
  degrades gracefully under added synthetic noise. This is the primary
  correctness gate — must pass before considering any Part done.
- Always normalize final M by M00; always run the realizability check
  (`trace(M^T@M) <= 4*M00^2`) as a diagnostic.
- Every function's docstring should cite which Hauge equation number(s) it
  implements (traceability for a thesis-adjacent tool).
- Match `measure.py`/`qwp_calibration.py`'s existing conventions for
  anything touching hardware in Part 1: dry-run support throughout,
  `MotorError`/`CameraError` exception types, retry-with-backoff, same
  output folder shape, `ask_yes_no` operator-confirmation pattern.
- Do not modify `measure.py` — Parts 2-4 only ever read its output.

## Deliverables checklist

1. `qwp_calibration.py` extended with null-search (Part 1).
2. `discrete_reconstruction.py` (Part 2).
3. `continuous_single_arm_reconstruction.py` + `continuous_single_arm_calibration.py` (Part 3).
4. `continuous_dual_arm_reconstruction.py` + `continuous_dual_arm_calibration.py` (Part 4) — priority.
5. Unit tests (synthetic round-trip, all four Parts).
6. A short top-level `README.md` for this new project explaining how the
   pieces connect and how to run each stage end-to-end.
