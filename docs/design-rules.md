# revela — design rules

The canonical statement of the rules this project is built on and the
conventions that keep it coherent. Read it before changing anything under
`revela/`.

These are design decisions, not style preferences: a change that breaks one of
them will be reverted even if it works. Each rule is stated with the reasoning,
because a rule whose reason is forgotten gets argued away in about six months.

## What this is

An image signal processing pipeline written in NumPy, from which synthesisable
Verilog is generated.

**revela is the ISP. [np2hw](https://github.com/lanserge/np2hw) is the
generator, and revela is its flagship proof case.** They are separate projects.
revela *depends* on np2hw — never vendor it, never fork it, never copy code out
of it.

When revela needs something np2hw cannot express, **the fix goes into np2hw, as a
generic capability at the NumPy level.** Not a revela-side workaround, not
hand-written Verilog smuggled in as a "wrapper", and not an ISP-shaped special
case bolted onto np2hw. If a block cannot be built, that is a feature request for
the compiler, and the block waits.

Python 3.11+. Dependencies: numpy, np2hw, pytest, cocotb, jsonschema. Verilator
for simulation. **Do not use Amaranth. Ask before adding any dependency beyond
these.**

---

## THE FOUR RULES

### 1. One model per block, written directly at the hardware's arithmetic

Integer dtypes with explicit widths. Explicit rounding. Explicit saturation. LUTs
where the hardware will have LUTs, shifts where the hardware will shift.

**No `np.float64` anywhere in `revela/blocks/`.**

This model IS the specification and IS the golden reference. There is **no float
reference model** and **no "float vs fixed" comparison anywhere in the library**.

That prohibition is the one people try hardest to argue their way around, so:
the hardware algorithm is a *genuinely different algorithm*, not a quantised
float one. Different rounding, different saturation points, different LUT
breakpoints, different order of operations. Comparing it to a float
implementation measures the distance between two different algorithms and then
calls the result "quantisation error", which it is not. It tells you nothing
about whether the hardware is correct, and it hides the errors that matter behind
a number that always looks acceptable.

Image quality is measured at **pipeline level, against reference images**. Never
per block.

`experiments/` is exempt and is where algorithm exploration and comparison
belongs. It may use float freely. **Nothing in `experiments/` is ever a
dependency of anything in `revela/`.**

### 2. Params are declared once, per block, and allocated globally

`params.py` defines `Param`: name, bit width, fractional bits, default,
description. One declaration drives four things:

- the arithmetic in the model,
- the CSR width and offset in the generated Verilog,
- the register map documentation,
- the host-side accessor.

**Blocks declare LOCAL offsets only — never absolute addresses.** `pipeline.py`
assigns each block **INSTANCE** a base address at composition time.

Non-negotiable, and here is the test of it: a stereo pipeline instantiates every
block twice, and blocks must be independently instantiable for unit tests. If
either is awkward, the allocation is wrong.

Each block base is aligned to a power of two (256 bytes), so address decode is a
**bit-slice compare** rather than a range comparator — cheaper in hardware, and
far more readable in the output. Address space is free; decode logic is not.

The host API mirrors the hierarchy: `dev.left.ccm.m00 = 512`.

### 3. Verification is bit-exact

The only per-block correctness test is **bit-exact agreement between the NumPy
model and the generated Verilog under Verilator via cocotb**. Identical, not
"close enough". No tolerance, no PSNR, no "within 1 LSB".

Framing is part of what must be exact. A block that produces correct pixels with
a wrong `eol` breaks every block downstream, so the testbenches check
`sof`/`eol`/`last` alongside the data, and they do it under **randomised
backpressure** — a block that only works when the sink never stalls is not
finished.

Image quality is measured at pipeline level against reference images. Never per
block.

### 4. Generated Verilog must be readable

Meaningful signal names. Module names matching the block. Comments carrying each
parameter's `description` through from `params.py`.

Somebody licensing this will read the output in review and hand it to their own
verification team. **Output readability is a product requirement, not a nicety.**
A generated file that is correct but unreadable has failed.

---

## Register map

**Block-owned config vs pipeline context.** These are different things and are
kept apart deliberately:

- **Block config** — CCM coefficients, gamma tables, WB gains. Lives in that
  block's register map. Written to a shadow register; committed to the live value
  at the **frame boundary**, so a frame is never processed with half-updated
  coefficients. A write made during frame N takes effect on frame N+1. Software
  that needs a change sooner has to wait for the boundary, not race it.
- **Pipeline context** — width, height, active window, Bayer phase, bit depth.
  Lives in a single `pipe` block at base 0 and is fanned out to blocks as
  **WIRES**, never duplicated as per-block CSRs. One width register per pipeline,
  read by everyone. The alternative — a width register in every block — makes a
  resolution change N writes that must all land in the same frame, and any block
  whose write is missed corrupts the image in a way that looks like a bug in that
  block.

**`pipe` is an ordinary block, not a special case in the code.** It has a
`ParamSet`, an ID-and-version word, and it is allocated through the same path as
everything else. It sits at base 0 by convention, not by a branch in the code.

**Statistics** go in a **separate address region with different structure**: a
memory-mapped RAM window for bulk reads, not scattered CSR words.
**Double-buffered per frame** — the host reads frame N while frame N+1
accumulates — and **NOT** commit-on-vsync like config params. That is the
opposite mechanism, solving the opposite problem: config protects the hardware
from a half-written host, statistics protect the host from a torn read.

**Every block has an ID-and-version word at local offset 0**, so the host can
verify the loaded bitstream matches the software before the first pixel moves.
The block ID is a stable 16-bit FNV-1a of the block name (derived, not a central
registry of magic numbers that generates merge conflicts); the version is
declared and bumped when the register layout moves.

**Emit a machine-readable register map (JSON) alongside the Verilog.** The host
API and the documentation are both generated from it. **Nothing anywhere
hardcodes an address** — no constant in a host script, no `#define` checked in,
no address in a docstring somebody might copy.

### The register file

`Pipeline.generate()` emits an AXI4-Lite slave in front of the datapath, so a
design is configurable on hardware rather than presenting a few hundred flat
parameter inputs. `generate(control=False)` stops at the datapath, which is what
a bit-exact testbench wants: it drives coefficients directly instead of writing
them over a bus.

**revela allocates the addresses; np2hw emits the decode.** revela hands np2hw a
list of `np2hw.Reg` — name, width, signedness, reset, access, and the byte offset
this project's allocator chose — and gets back the register file and the wrapper.
Neither side holds a copy of the other's half, and that is the only reason the
JSON map and the hardware cannot drift apart. A register file that assigned its
own offsets would be a second address map, correct on the day it was written.

Consequences worth stating, because each one is something a host can rely on:

- **Every register the map publishes is decoded at that address**, including the
  registers of a block that is declared but not built yet. The map and the
  decode are one document. Such a block is flagged `"implemented": false`, and
  its registers read back what was written and reach no datapath — software is
  told, rather than discovering it from an image that did not change.
- **The ID-and-version word is a read-only constant** wired into the decode, not
  a register. It has no write path at all.
- **A write to a read-only word, and any access to an unmapped address, is
  answered SLVERR.** A silently dropped write presents as a configuration that
  had no effect, hours later and somewhere else.
- **A signed register reads back sign-extended** to the bus width. A host that
  wrote −100 and read back 65436 has been told a different thing from what it
  wrote, and it is the register file that chose the width.

Statistics windows are declared and allocated but have no hardware behind them
yet: `stats` is the one block np2hw cannot trace, so there is nothing writing a
window to double-buffer. The region is in the map; the RAM is not in the RTL.

## Pipeline descriptions

A pipeline's structure is a NETLIST in JSON -- `revela/designs/schema.json`,
versioned and validated in CI exactly like the sensor schema -- built with
`revela.designs.load()`. This is the form a pipeline builder GUI would edit.

**A netlist, not a list, because a real ISP is not a chain.** Statistics TAP the
datapath and produce no pixels. A preview path FORKS off the main one. Luma and
chroma SPLIT and only luma is sharpened. HDR MERGES two exposures. Forcing that
into an ordered list puts blocks in the datapath that are only watching it --
which is exactly the error a list encourages.

Blocks therefore declare their stream PORTS (`revela.blocks.Ports`) rather than
being assumed one-in-one-out. A block with no outputs is a SINK: it observes a
stream and never stalls it, so its `ready` is not wired back and any number of
taps off one output are free.

The graph is validated before anything is emitted, and each check is a real
hardware failure rather than a tidiness rule:

- an undriven block input is a stream that never arrives;
- two drivers on one input is a short;
- a cycle cannot be scheduled in a feed-forward pipeline;
- a fork to two consumers that BOTH apply backpressure needs an element that
  buffers one side while the other stalls. No such element exists yet, so the
  fork is refused rather than emitted as something that deadlocks the first
  time one branch stalls -- a bug that only appears under load.

The checks themselves live in np2hw (`np2hw.netlist`), because they are laws of
np2hw's streaming handshake and a copy of them here would be free to disagree
with the emitter -- which is exactly what happened before they moved. revela
declares what each node MEANS; np2hw decides whether the graph is buildable.

**There are three JSON files and they point in different directions:**

    pipeline description   INPUT    structure, no addresses   revela/designs/
    profile                INPUT    values, no structure      revela/profiles/
    register map           OUTPUT   addresses, generated      revela/compose.py

**Neither input can express an address**, and both schemas reject an `addresses`
key outright. Blocks declare local offsets; revela assigns each block instance a
base at composition time. If an input could pin an address, a builder could emit
a map that silently disagreed with the hardware it generated -- the exact failure
rule 2 exists to prevent. Region *bases* are a platform choice and may be set;
individual addresses never are.

Allocation must therefore be a pure function of structure, and there is a test
that proves it: a description recovered from a built pipeline, rebuilt, produces
byte-identical Verilog and an identical register map. Note that allocation
follows DECLARATION order, not topological order -- `Pipeline.nodes` versus
`Pipeline.datapath` -- and conflating the two silently changes every address.

A design that runs the same chain twice describes it once, as a `subsystem`,
and instantiates it by name. Addresses are still allocated per INSTANCE, so the
register map is byte-identical to spelling the graph out -- only the emitted RTL
changes, from N copies of a module to one instantiated N times. A subsystem's
boundary port is a name, not a component: it joins two edges into one, so the
pipeline graph stays flat and allocation knows nothing about subsystems.

Designs live in `pipelines/<topology>/<sensor>/<variant>/`, one directory each:
`pipeline.json`, a `profiles/` directory, and a git-ignored `build/`. A profile
carries register VALUES and names the sensor it was tuned for, so one structure
serves several sensors and several scenes. It cannot add a block or reorder the
graph. Values layer as block reset -> sensor-derived -> profile, and the resolved
settings record which layer each value came from.

Blocks are resolved by name through `revela.blocks.registry()`, which discovers
every module declaring a `ParamSet`. A declared stub has no `ParamSet` and so
cannot be named in a description -- better to fail at composition than to build a
pipeline containing a block that has no model and cannot be verified.

## Stream interface

`valid / ready / data / sof / eol / last`, parametrised on bit depth and channel
count. Flags ride **with** the pixel they describe, qualified by `valid` — they
are not separate pulses to be counted.

Blocks are chained by **direct streaming**, not a bus: `in → s0 → block → s1 →
block → out`. No AXI between stages, no DRAM round-trip. One pixel per clock,
with real backpressure that composes all the way back to the sensor interface.
AXI4-Stream Video exists **only as an adapter at the pipeline boundary**, where
you are talking to somebody else's IP.

Line buffers are **per block, not shared across blocks**. That is a deliberate
trade: it costs BRAM on a long chain of stencils, and it is what makes rule 3
possible, because a block with its own buffers can be instantiated alone and held
bit-exact against its model. If fusion is ever needed, the route is to trace a
sub-chain as ONE np2hw expression (np2hw already hash-conses and shares line
buffers within a single traced expression) — a composition choice in
`pipeline.py`, not an architectural rewrite.

## Sensors

`revela/sensors/<name>/sensor.json`, validated against `schema.json` by a pytest
test so bad contributions fail CI, not hardware. The schema is versioned.

Contents: Bayer phase, black level pedestal, bit depth and packing, frame timing
(line length, frame length, line time), the exposure formula (exposure time →
coarse/fine integration registers), the gain code mapping (Sony parts are
typically `256/(256-code)`), and register sequences per mode.

**Where each part is consumed:**

- **Software at runtime** — exposure/gain conversion, mode selection, I2C
  sequences. The 3A loop reads the JSON and computes the writes. This is most of
  the file.
- **Build-time parameters** — bit depth (datapath width) and image width (line
  buffer sizing). **Only these.**
- **Generated logic, ONE case** — on a headless target with no CPU, generate an
  I2C init sequencer from the register sequences: a ROM of register writes plus
  the state machine that walks it. A good demonstration of np2hw beyond datapath.

**Prefer runtime registers over build-time generation.** Bayer phase as a two-bit
register costs almost nothing and lets one bitstream serve every sensor. **Never
generate a different pipeline per sensor** — that turns the verification matrix
into sensors × modes and it will not stay green.

**Separate model-level facts from per-unit calibration.** `sensor.json` holds
what is true of *every* part of that model. A specific unit's CCM, lens shading
mesh and defect map are **calibration outputs, loaded at runtime, and do NOT live
in the repo.** The schema rejects a `calibration` key outright.

**Do not copy register tables out of Linux kernel drivers.** `imx219.c` and
friends are GPL-2.0, which is incompatible with this project's licence. Derive
from datasheets, or reference the driver without transcribing it. The schema has
a `provenance.gpl_driver_transcribed` field that must be `false`.

## Licensing

Solderpad Hardware License v2.1 (`LICENSE`), a wraparound over Apache 2.0
(`LICENSE-APACHE`), plus `NOTICE`. `pyproject.toml` declares
`license = "Apache-2.0 WITH SHL-2.1"`.

**Every source file starts with:**

```python
# Copyright <year> <author>
# SPDX-License-Identifier: Apache-2.0 WITH SHL-2.1
```

`tests/test_spdx_headers.py` fails if any `.py` under `revela/` is missing it.

Generated Verilog carries the same licence, because its datapath is derived from
the models; generated files say so in their header. What np2hw emits around it is
covered by that project's Output Exception and carries no obligation. See
[LICENSING.md](LICENSING.md).

A commercial licence with support and indemnity is offered alongside, which is
why every contributor signs the [CLA](../CLA.md). It is a licence grant, not an
assignment — contributors keep their copyright — but it must be sublicensable and
transferable, or a commercial tier is impossible to add later without
re-contacting everyone who ever touched a file.

The name is not covered by the licence: see [TRADEMARK.md](../TRADEMARK.md).

## Layout

```
revela/
  params.py       Param, Context, StatsWindow, ParamSet, AddressAllocator
  stream.py       stream interface + AXI4-Stream Video adapter
  pipeline.py     composition, address allocation, JSON map emission
  fusesoc.py      design packs: pipeline JSON -> .core + Verilog + maps
  blocks/         one file per block; the decorated model IS the block
                  (its .params is the register set, .run() the reference,
                  .generate() the RTL)
    pipe.py       pipeline context (an ordinary block, at base 0)
    blacklevel.py complete: model + generation + bit-exact test
    whitebalance.py complete; per-CFA gain, fixed-point variants
    gamma.py      complete; PWL tone curve from a knot register array
    stats.py      model + register map; RTL pending np2hw reductions
    ccm.py        complete; first 3-channel block, seatless until demosaic
    demosaic/
      bilinear.py complete; CFA-position-selected taps on one shared window,
                  three channels out via np.stack -- the model never packs
      bicubic.py  complete; Keys half-phase cubic per lattice, 7x7 window,
                  signed accumulators, floor shifts, clipped overshoot
      malvar.py   stub; the usual default, gradient-corrected 5x5
      menon.py    stub; directional with refinement, the quality tier
    ...           stubs with intent documented
  sensors/        schema.json + <name>/sensor.json
  control/        AE/AWB/AF — pure Python, frame rate, no hardware
  host/           transports (spi, udp, pynq) + generated register accessors
tests/            pytest + cocotb testbenches
experiments/      exploratory work, NOT part of the library, float allowed
boards/           tang-primer-20k/, pynq-z2/
```

## Current status and known gaps

- `blacklevel` is complete end to end.
- `stats` has a model and register map but **no generated RTL**: accumulation
  over a region is a reduction, which np2hw does not trace yet. It is the one
  block that does not satisfy rule 3, and that is stated rather than hidden
  behind a test that only exercises the NumPy side. **Do not "fix" this by
  writing Verilog by hand.**
- **No block contains hand-written Verilog.** `blacklevel` is one np2hw trace of
  its model: the phase select, the coefficient mux and the datapath all come out
  of `planes()`. np2hw traces strided slices with a register-valued phase
  (`out[py::2, px::2] = ...`), and because the four planes partition the image it
  lowers them to ONE full-rate datapath with the coefficient selected by pixel
  position, not four quarter-rate paths.
- **A block is a decorated function.** `@ispblock` declares what the arithmetic
  cannot say -- the register set, what each stream MEANS (its domain), which
  context bits the model takes -- and `Block.generate()` is generic, shared by
  every block. Stream WIDTHS are deliberately not declared: np2hw derives them
  from the trace, and declaring them twice is how `in_flags` and `ctx_ports`
  went wrong. `Pipeline` holds Blocks, not modules; the registry discovers
  Blocks; a block that cannot be traced yet says WHY in `not_traceable`.
- **Domains are checked in the netlist.** A 12-bit Bayer stream and a 12-bit
  luma stream are identical to a compiler and nonsense to connect, so a block
  declares its ports' domain and `np2hw.compose()` refuses a mismatch. np2hw
  holds the domain as an opaque tag it compares and never interprets — ISP
  semantics stay here, the mechanism stays there. Component count follows from
  the domain, so no block writes its own channel guard.
## The revela / np2hw interface

Four things cross the boundary, and nothing else:

    revela -> np2hw   Param, Image2D, to_ir(model, image, *params)
                      compose(instances, connections, ports)
    np2hw -> revela   Core (verilog, module, interface, line_buffers, ...)

**A module describes itself.** `Core.interface` states its clock and reset names,
which framing flags it accepts, which it regenerates, its parameter ports and
their naming convention. revela reads that; it never models np2hw's conventions.
It used to, and that drifted twice in one sitting -- both times surfacing as an
elaboration error far from the change that caused it.

**Composition nests.** `compose()` returns an interface of its own, so a composed
module is instantiable inside another `compose()`. A reusable front end is built
once and instantiated per sensor rather than having its graph copied into every
top level. Single-stream subsystems nest; a multi-stream one is composable at the
top and says `nestable: false` rather than silently exposing its first stream.

**Descriptions travel with the parameter**, through the IR, into the generated
port comment. There is no side channel: a shaped `Param` carries `labels`, so its
leaves describe themselves (`Gr: ...`) instead of all repeating the parent text.

**Names are stated, not assumed.** `param_prefix` in the interface is why a
composed module (ports named literally) and a generated core (ports prefixed
`param_`) can both be instantiated by the same composer.

The general rule behind all of it: **whoever writes a thing owns its
description.** np2hw writes the ports, so np2hw publishes the port list. revela
declares what a stream MEANS, so revela publishes the domain -- and np2hw holds
it as an opaque tag it compares and never interprets.

- **revela emits no Verilog at all.** Block datapaths are traced from the models
  by np2hw; the top level is built by `np2hw.compose()`, which instantiates the
  generated cores and wires the netlist. revela supplies structure, declarations
  and descriptions — the only strings it builds are `//` comment lines (the SPDX
  header and the address map) that it hands to the composer as documentation.
- A generated core now DESCRIBES ITSELF (`meta["interface"]`): which framing
  flags it accepts, which it regenerates, what its parameter ports are called.
  revela used to model that separately and it drifted twice in one sitting, both
  times surfacing as an elaboration error far from the change that caused it.
  Whoever writes the ports owns the port list.
- `compose()` type-checks every net before emitting a line: a source and sink
  must agree on width and framing. Connecting a 10-bit Bayer stream to a block
  built for 12-bit RGB used to elaborate happily.
- Still missing, deliberately: a **buffered fork** (a fan-out to two consumers
  that both apply backpressure is refused rather than emitted as something that
  deadlocks under load), and a **register file** — the top still exposes flat
  `param_*` inputs, with no CSR block, shadow registers, commit-on-SOF or stats
  RAM windows behind them.
- `demosaic/bilinear` is complete: the first phase-selected STENCIL block
  (np2hw traces `out[r::2, c::2] = taps[r::2, c::2]` to one shared window
  with a positional tap-combination mux) and the first three-channel
  producer (`np.stack([r, g, b], axis=-1)`; the wire word is the stream
  layer's business). `malvar` and `menon` remain declared stubs.
