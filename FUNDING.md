# Funding revela

The short version is in the [README](README.md#funding). This is the detail that
does not belong there.

revela is developed independently. It is released openly under the
Solderpad Hardware License v2.1 and will stay that way: nothing on this page is
contingent on the open project being reduced, and no block that has been released
openly is withdrawn to a paid tier.

Enquiries about anything below: **s.rabykin@gmail.com**. A direct email, not a
contact form — say what you are building and what is blocking you, and you will
get an answer from the person who wrote the code.

---

## Commercial licence

For organisations that cannot ship on the open licence alone. Typically that is a
procurement or legal requirement rather than a technical one.

### What it includes

- **An express patent grant**, from the maintainer, covering the licensed work.
- **Indemnity** against third-party intellectual property claims arising from the
  licensed work, on terms and to a cap agreed in the contract.
- **A support SLA** — a defined response time, a named contact, and an agreed
  path for escalating a bug that blocks your tape-out or release.
- **Advanced blocks** not present in the open tree, and the ones that are
  present in a more complete form.
- **Verification collateral**: the constrained-random testbenches, coverage
  models, regression results and traceability from requirement to test that an
  external verification team will ask for and that the open repository does not
  carry.
- **Warranty terms** other than "AS IS", to the extent agreed.

### What it does not include

Stated as plainly as the inclusions, because a funding page that only lists what
you get is not useful:

- **It is not a different codebase.** The commercial tier is the same models and
  the same generator. There is no secret fork with better arithmetic.
- **It does not remove your obligations under the open licence** for anything you
  obtained under it, and it does not retroactively change the terms of code
  already released.
- **It is not a silicon guarantee.** Bit-exactness between the NumPy model and
  the generated RTL is verified and is the project's central claim. Whether the
  result meets timing in your process, at your clock, in your floorplan, is
  yours.
- **It does not include image-quality tuning for your camera.** That is
  consulting, below. A licence buys you the pipeline; it does not buy you a CCM
  for your lens.
- **It does not confer rights to the name.** See [TRADEMARK.md](TRADEMARK.md).

Pricing is per-project and depends on scope, seat count and indemnity cap. Ask.

---

## Sponsor a block

The roadmap is in the tree: every block not yet built exists as a declared
stub in `revela/blocks/`, with its intent documented -- what it corrects, the
intended arithmetic, where its parameters come from, and where it sits in the
pipeline. Sponsoring one funds turning that stub into a complete block: the
NumPy model at the hardware's arithmetic, the generated Verilog, and a
bit-exact testbench under randomised backpressure.

Currently declared stubs: **lsc**, **defect**, **demosaic**, **rgb2yuv**,
**sharpen**. The stub files are the specifications; they are not restated
here, so this page cannot drift from them.

Improvements to existing blocks are sponsorable the same way where the gap is
documented. The standing ones:

- **stats RTL** -- the model and the register map exist; generation waits on
  reductions in np2hw, so this is really compiler work (below).
- **Status registers** -- live datapath state (sticky overflow flags, frame
  counters) readable over AXI4-Lite.

### How it works

The same shape as sponsoring a sensor, minus the bench:

1. **You pick the target and say what you need it for.** A demosaic has
   quality tiers; knowing what you are building changes the scope honestly.
2. **Scope and cost are agreed in writing**, including what "done" means --
   and here "done" is unusually concrete: the four rules in
   [docs/design-rules.md](docs/design-rules.md). A sponsored block ships when
   its model-versus-RTL suite is bit-exact under randomised backpressure and
   its generated Verilog is readable. Not before, and no payment changes
   that bar.
3. **The work lands in the open tree immediately**, under the project's
   licence, with named credit in the release notes unless you would rather
   not be named. Sponsorship buys existence and ordering, not exclusivity --
   a block held privately would be a fork of the project's own roadmap.

A block your product needs that is *not* on the roadmap -- specific to your
camera rather than to every ISP -- is consulting, below, where open release
is agreed per engagement. The dividing line: a stub is the project's own
promise, so funding one is always open.

### Compiler capabilities (np2hw)

revela generates no hardware itself -- every line of Verilog is rendered by
[np2hw](https://github.com/lanserge/np2hw) -- so every missing compiler
capability is a revela gap wearing a different name. np2hw's README is the
authoritative list of its sponsorable targets; what each one unlocks *here*:

- **Reductions** -- `stats` gets its RTL, and the AE/AWB loops close on
  hardware instead of in simulation: the last piece between this tree and a
  camera that exposes itself.
- **Status registers** -- the receiver's sticky overflow flag and frame
  counters become host-readable, so bring-up reads the answer instead of
  guessing whether pixels arrived.
- **Buffered forks** -- taps that consume at their own rate (a preview
  scaler, a histogram) without being able to stall the image path.
- **Multi-stream tracing** -- blocks with more than one stream become
  traceable: dual-exposure fusion, a stereo merge.
- **Bypass-aware power gating** -- every block with an enable gets its
  disabled logic cone isolated and clock-gated, savings measured by toggle
  counts -- block-level power control across the whole pipeline, which is
  what a battery-powered camera actually asks of an ISP.

np2hw is MIT-licensed and public today; sponsoring a capability there
unblocks every np2hw user, and revela inherits it the release after it
lands.

---

## Sponsor a sensor

The bottleneck on supporting a sensor is not code, it is **measurement**. A
`sensor.json` written from a datasheet gets you exposure and gain conversion. It
does not get you the black level pedestal at each analogue gain, the read noise
and full-well that set the AE limits, the linearity knee, or a colour matrix — all
of which come from putting the part on a bench under known illuminants and
measuring it.

Sponsoring one funds exactly that work.

### How it works

1. **You choose the sensor and the test conditions.** Which part, which modes,
   which illuminants, which temperature range, and what specifically you need
   characterised. If your product runs at 4000 K under a particular LED, that is
   what gets measured.
2. **Scope and cost are agreed in writing** before anything starts, including
   what a "result" is — the measurements taken, the format they arrive in, and
   what happens if the part turns out not to behave as its datasheet claims.
3. **You get the data first**, under an agreed exclusivity window, along with the
   raw captures and the method — not just the fitted numbers, so your own team
   can check the work rather than trusting it.
4. **You get named credit**, in the sensor's directory and in the release notes,
   unless you would rather not be named.
5. **The data is then published here, openly, for everyone**, under the project's
   licence. That is the point, and it is not negotiable: sponsorship buys you
   priority and a head start, not exclusivity in perpetuity. A characterisation
   that stays private helps one company once; published, it stops the next person
   repeating the bench work.

Per-unit calibration for *your* production line — the CCM, the shading mesh and
the defect map of each physical camera — is a different thing and stays yours. The
project only ever publishes what is true of every part of a model. See
[CONTRIBUTING.md](CONTRIBUTING.md) on why the schema rejects a `calibration` key
outright.

---

## Consulting

Direct engagement, billed by the day or by a fixed-scope statement of work:

- **ISP bring-up** — getting a pipeline running against your sensor and your
  fabric, including the sensor interface, the register map integration and the
  host software.
- **Tuning** — black level, lens shading, white balance, colour matrix, gamma and
  sharpening for your optics and your target look, against your reference images.
- **Custom blocks** — a block your product needs that the open tree does not
  have, written as a NumPy model with bit-exact generated RTL and its own
  verification, to the same four rules as everything else in
  [docs/design-rules.md](docs/design-rules.md).
- **Review** — of an existing pipeline, an integration, or a verification plan.

Whether a custom block is released openly is agreed per engagement, up front.
Both answers are available; the one you pick affects the price.

---

## GitHub Sponsors

[github.com/sponsors/lanserge](https://github.com/sponsors/lanserge) — recurring
support for the open project, with no contract and no commitment either way.

Tiers and what each unlocks:

| Tier | Per month | What it unlocks |
| --- | --- | --- |
| **Supporter** | $5 | Name in `SPONSORS.md`. Nothing else, honestly — it funds the work, not a product. |
| **Backer** | $25 | The above, plus your name in the release notes of every release your sponsorship covers. |
| **Sustainer** | $100 | The above, plus a monthly written development update ahead of the public one, and your issues and feature requests read first. Read first is not the same as done first; the roadmap is still the roadmap. |
| **Partner** | $500 | The above, plus a monthly call, early access to branches before they merge, and input into roadmap ordering. |
| **Organisation** | $2,000 | The above, plus your logo in the README, priority on bug reports that block you, and a standing discount on consulting days. |

Sponsorship at any tier is **not** a commercial licence, does not include
indemnity or a support SLA, and does not confer rights to the name. Those are the
commercial licence, above; they need a contract, and a sponsorship button is not
one.

---

## What funding does not buy

- **A change to the four rules.** No amount of money buys a float reference
  model, a block without bit-exact verification, or a register map with a
  hardcoded address in it. Those rules are why the project is worth funding.
- **Register tables transcribed out of GPL-2.0 kernel drivers.** The licence
  incompatibility is not a matter of preference. See
  [CONTRIBUTING.md](CONTRIBUTING.md).
- **Silence about a defect.** If a bug affects other users, it gets reported and
  fixed in the open, whoever found it and whoever paid for the fix.
