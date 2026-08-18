# Section II — QWP retardance calibration

**Question this answers:** what is the *actual* retardance (δ) and
diattenuation (T) of each QWP, in its real mounted position — not the 90°/
ideal value you'd assume from the spec sheet?

| File | What it is |
|---|---|
| `qwp_calibration.py` | Null-search + three-angle closed-form retardance measurement (Hauge Eqs. 19-21, Eq. 4). The only file you run. |
| `test_qwp_calibration.py` | Hardware-independent tests (see Testing, below). |

## The physical procedure (what the code is actually doing)

1. **Arms collinear, both QWPs physically removed.** The first polarizer
   (`PSG_Polarizer`) is the reference — call whatever angle it's sitting at
   "optical 0°". You never need to know its true orientation in the lab
   frame, and **no external polarimeter is used anywhere in this
   procedure** — the camera itself is the null detector throughout.
2. **Null the analyzer against the polarizer**: rotate `PSA_Analyzer`
   while reading the camera's ROI-mean intensity, and find the angle of
   *minimum* intensity (crossed polarizers, Malus's law). That angle
   *defines* "optical 90°" for the analyzer.
3. **Insert one QWP** between the still-crossed P/A pair, and null *it*
   the same way: rotate the QWP, watch intensity, find the minimum. That
   angle *defines* "optical 0°" (the QWP's own fast/slow axis reference)
   for that specific QWP.
4. **Confirm the other QWP is still removed**, then measure R(C) at the
   just-discovered reference (P and A both parallel at optical 0°) —
   Hauge Eq. 19. Two modes (`--calibration-angle-mode`):
   - `three_angle` (default): exactly 0°, 45°, 90° — solve directly for
     s, f (Eq. 21), then r, δ, T (Eq. 4). No iteration, no fitting, just
     algebra; 3 equations, 2 unknowns, zero redundancy.
   - `least_squares`: N angles (`--num-calibration-angles`, default 12)
     spread across one full period, least-squares fit instead of an
     exact solve — see "Want lower-noise retardance numbers?" below.
5. **Swap**: physically remove the first QWP, insert the second, repeat
   steps 3-4 for it (step 2 is *not* repeated — the analyzer's null only
   needs finding once per session).

### How "null" is found and trusted (not just guessed)

Two-stage search, run automatically by `search_null_automated()`:
- **Coarse**: sample the whole range (every 5° across a full 360°) and
  keep the lowest reading. This is what protects you from a single noisy
  low reading looking like the null — you're comparing broadly first, not
  trusting one point.
- **Fine**: golden-section search within that neighborhood, down to 0.05°
  by default. Both intensity models here (crossed-polarizer Malus's law,
  and the crossed-compensator formula) are smooth and single-minimum in
  the relevant range, so this refinement step is guaranteed to converge to
  the true minimum, not get stuck on a local dip.
- Every "reading" is already an average over a whole ROI (thousands of
  pixels), which is a first line of noise reduction before the search
  logic even runs.

An interactive fallback (`--null-search-mode interactive`) is available if
you'd rather nudge the angle by hand and watch the live readout.

### Want lower-noise retardance numbers?

The 3-angle closed form is the *minimal* exact solve — 3 equations, 2
unknowns (s, f), zero redundancy, so all of its noise goes straight into
the answer. `--calibration-angle-mode least_squares` fixes this the same
way Section IV/V already do elsewhere in this project: sample *more*
angles (`--num-calibration-angles`, e.g. 12) and least-squares fit
`R(C) = A0 + A2·cos(2C) + A4·cos(4C)` (the same Eq. 21 relations,
generalized — `s = A2/(A0+A4)`, `f = 2·A4/(A0+A4)`, reducing to the exact
3-point answer when N=3) instead of solving exactly 3 points. Averaging
over more captures lowers the standard deviation of the recovered s, f the
same way any over-determined fit does — confirmed in testing (`s`'s
per-pixel std roughly halved going from 3 to 12 angles on the same
synthetic bench).

With N ≥ 5 angles, there's a bonus: a *separate* fit of the full 5-term
model (`+ B2·sin(2C) + B4·sin(4C)`) reports B2, B4 as a genuine,
independent alignment check — they're exactly 0 in theory at this P=A=0°
reference, so a large value means P or A isn't actually sitting at its
assumed reference, not that the compensator itself is bad. (Below N=5
this diagnostic isn't computable at all — 5 unknowns need at least 5
equations — so it's reported as unavailable rather than guessed at.)

One caveat already found and fixed: angles spread *evenly* across the
full 0-180° period alias at exactly N=3 (60° spacing makes `cos(2·60°)`
and `cos(4·60°)` coincide, the same aliasing class as QWP angles spaced
90° apart elsewhere in this project) — `least_squares_calibration_angles()`
special-cases N=3 back to Hauge's own 0°/45°/90° for that reason; N≥4 is
unaffected (verified full-rank for N=4 through 15).

## Output

`Data/QWP_Calibration/<date>_<target>_NN/Config/calibration_result.json`
— both QWPs' `s,f,r,delta_deg,T` (ROI summary + full per-pixel `.npy`
maps), plus `discovered_zero_offsets.json` (the null-search's discovered
motor-to-optical offsets, diffed against your assumed `ZERO_OFFSETS_DEG`).
This is the file every reconstruction section (III, IV, V) reads for the
*real* QWP defect parameters, instead of assuming an ideal 90° retarder.

## Running it

```powershell
python qwp_calibration.py --dry-run --no-prompt --target both

# Lower-noise variant: 12 angles, least-squares fit instead of the exact 3-point solve.
python qwp_calibration.py --dry-run --no-prompt --target both `
    --calibration-angle-mode least_squares --num-calibration-angles 12
```

`--target` accepts `PSG_QWP`, `PSA_QWP`, or `both` (default — calibrates
both, back-to-back, in one session with an operator-confirmed physical
swap in between). Drop `--dry-run` only after the checklist in
`../common/README.md` is done and a dry-run pass looks sane.

## Testing

```powershell
python -m unittest test_qwp_calibration -v
```

Hardware-independent: the closed-form Eq. 19-21/Eq. 4 math, the
golden-section search against a known synthetic minimum, a full
dry-run session (`DryRunOpticalBench`'s hidden ground-truth model — a
synthetic optical bench with a deliberately-imperfect, not-quite-90°
QWP and a deliberately-wrong assumed zero-offset, so the search has
something real and non-trivial to find) exercising both QWPs' null search
back-to-back, and the least-squares mode's own synthetic round-trip tests
(known-defect recovery at N=3 through many angles, exact agreement with
the 3-point closed form at N=3 on the literal Hauge angles, a direct
noise-reduction check that more angles lower the recovered-s standard
deviation, the B2/B4 alignment-diagnostic availability rule, and the
N=3-aliasing regression case above).
