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

Python 3.11+. Dependencies: numpy, np2hw, pytest, cocotb, jsonschema,
systemrdl-compiler (for checking the emitted SystemRDL). Verilator
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

**Blocks declare LOCAL offsets only — never absolute addresses.**
np2hw's composer (`np2hw.pipeline.compose`) assigns each block **INSTANCE** a base address at
composition time.

Non-negotiable, and here is the test of it: a stereo pipeline instantiates every
block twice, and blocks must be independently instantiable for unit tests. If
either is awkward, the allocation is wrong.

Each block base is aligned to a power of two (256 bytes), so address decode is a
**bit-slice compare** rather than a range comparator — cheaper in hardware, and
far more readable in the output. Address space is free; decode logic is not.

The host API mirrors the hierarchy: `dev.left.ccm.m_0_0 = 512`.

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
  block's register map. Written to a shadow register and committed as ONE
  batch when software writes `pipe.commit`; the datapath takes the batch at the
  next **frame boundary** and clears the bit, so a frame is never processed with
  half-updated coefficients. While a batch is pending, coefficient writes are
  refused, so the batch cannot change under the commit.
- **Pipeline context** — the facts every block shares: width, height, active
  window, Bayer phase, bit depth. Each comes from ONE place: those named in
  `stream.context` arrive with the stream, and the rest (the active window) are
  registers in a single `pipe` block at base 0. Either way they are fanned out
  to blocks as **WIRES**, never duplicated as per-block CSRs. One width register per pipeline,
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

The map also states how each register **faces a person**: its raw representable
range, and the widget a control surface should present — a tick box for one bit,
a choice among named values, a fixed-point box in the declared Q format, an
integer slider otherwise. That kind is DERIVED from the declaration by one rule
(`Param.widget`), never stated per parameter, because a register whose
presentation disagrees with its arithmetic is a register the interface lies
about. The single presentation fact no rule can derive — which raw values are
meaningful and what a person calls them — is declared as `Param.choices` and
validated where it is written: values in range, labels unique, the reset among
them. A panel renders the map and decides nothing.

### The register file

`np2hw.pipeline.compose.generate(design)` emits an AXI4-Lite slave in front of the
datapath, so a design is configurable on hardware rather than presenting a few
hundred flat parameter inputs. `generate(control=False)` stops at the datapath, which is what
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

A pipeline's structure is a NETLIST in JSON: a composite, validated against
`revela/designs/blocks.schema.json` on load, and built with
`np2hw.pipeline.load()`. This is the form a pipeline builder GUI would edit.

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

**There are three kinds of JSON and they point in different directions:**

    design (composite)     INPUT    structure, no addresses   revela/pipelines/
    profile                INPUT    values, no structure      revela/sensors/
    register map           OUTPUT   addresses, generated      np2hw.pipeline.compose

**Neither input can express an address**: the schema has nowhere to write one.
Blocks declare local offsets; revela assigns each block instance a base at
composition time. If an input could pin an address, a builder could emit a map
that silently disagreed with the hardware it generated -- the exact failure
rule 2 exists to prevent. Region *bases* are a platform choice; individual
addresses never are.

Allocation must therefore be a pure function of structure, and there is a test
that proves it: the same design built twice is the same Verilog at the same
addresses, and writing its connections in another order moves nothing. Note
that allocation follows DECLARATION order, not topological order --
`compose.stages` versus `compose.datapath` -- and conflating the two silently
changes every address.

A design is a composite: blocks and composites as nodes, referenced as
`package.module:block`, and the connections between their ports. A design that
runs the same chain twice describes it once, as a composite, and instantiates
it twice. Addresses are still allocated per INSTANCE, so the register map is
byte-identical to spelling the graph out -- only the emitted RTL changes: a
composite instantiated more than once, built alike and made of blocks alone,
is one module per block instantiated per instance. A composite's boundary port
is a name, not a component: it joins two edges into one, so the pipeline graph
stays flat and allocation knows nothing about the hierarchy.

Which block fills a node can be a build parameter like any other. A node's
`block` is a reference, or -- a bare name -- one of its composite's own build
parameters, whose value is an expression naming a block: `@package.module:block`
marks a reference inside an expression, so `"@revela.blocks.whitebalance"`,
`"wb_choice"` or `"@a.b if fast else @c.d"` all say which block fills it. Whatever
uses the composite sets that parameter through `build`, the outermost setting
winning, so a generic composite fills a node with a default and its user chooses
otherwise without editing it. The block chosen must present the ports of the
default, since everything around the node was wired to them; a setting the new
block has nowhere to put is refused.

A block may be described apart from its implementations. Its entry states its
parameters and ports, and under `implementations` the ways to realise it -- from
different vendors, different IPs for one tool, our own HDL -- each with its tool's
facts (`vivado`, `sources`), its own names for the block's ports, its own
settings as expressions over the block's parameters, and what follows from it
(`facts`). The block's `implementation` parameter chooses one, set like any other.
A design is wired, and checked, by the block's ports and never learns which
implementation filled it; the emitter renames each port to the implementation's.
A block's constraints file may be shipped in a package (`np2hw.hdl:np2hw_cdc_fifo.xdc`)
and `scoped_to` a module: one file then constrains every instance of that module by
the module's own names, so a crossing carries its timing intent wherever it is
placed. A port may exist only `when` an expression over the block's parameters holds (a
clock's phase-shift handshake), and an input of the implementation that no port
reaches is held by its `tie`. Each vendor implementation is held to its
description by the build: once the block design is built, every described cell's
pins are dumped and compared with what the description says they are
(`np2hw.toolchain.vivado.vendor_check`), and a description that is not true
stops the build before synthesis. What an implementation states is read as `node.fact` -- the HDMI front end's
`hdmi_rx.lane_map` is dvi2rgb's lane order, and the bayerlink receiver's lanes
are assigned from it. A block whose only implementation is a composite or a model
needs no separate description: its ports and its trace are its owner's own word.

A loaded design is a netlist (`np2hw.design.netlist`), built once: every leaf
with its implementation, settings and ports as the build resolves them, every
connection as a bundle -- a stream or a bus, joined by name -- or a net of pins,
and the board's external pins. The wiring check reads it, and the build writes it
out as `netlist.json` beside everything else, for any tool that wants the design
without loading it.

A design's register bus is its own, wired like any other connection: the
processor's port onto np2hw's AXI3 bridge, a decoder (`np2hw.bus:axil_decoder`)
onto every slave, an arbiter where a register file has a second master, a reset
synchroniser per clock domain (`np2hw.hdl.bus`). No address is written down. Each
slave states its window as a fact, `node.port.window` -- two to the power of its
register port's address width, at least a page -- the decoder is built with those
sizes and the processor's region (a fact of the processor's block), and np2hw's
allocator places them. The decoder publishes where each landed (its fact
`windows`); the toolchain sets each window in the block design, through any block
that only forwards (`forwards`), and the build writes the same windows, by cell,
to `address_map.json` beside the bitstream. The hardware handoff cannot see
through our decoder, so the host reads its addresses from that file, never from
the `.hwh`. Every register slave sits on one bus clock, the processor's; a
register file whose datapath runs elsewhere crosses to it itself.

An expression may call a function its library offers, named like a block:
`"@np2hw.video_out:pixel_khz(mode)"` is the raster's pixel clock, read from
np2hw's mode table. A function is callable only if it says so
(`fn.expression = True`): a design is data, and what it can run is what its
library offers it -- a table's lookup, never whatever happens to be importable.
So a board's choices are parameters of the design, computed where they are
stated, with no step beside the design supplying them.

A parameter carries a fact both ways. Down: a composite declares it and wires it
into its blocks by expressions, as any build setting. Up: a composite assigns it a
fact of one of its nodes, `node.port.fact` -- `bits`, `channels` or `data_bits` of
a stream, `max_width` or `max_height` of one entering. On an input that is the fact
declared where the stream enters; on an output it is traced, so the node is built
to read it. So the board's `sample_bits` is `isp.isp_in.bits` and its
`isp_out_bits` is `isp.isp_out.data_bits`, wired from there into everything that
carries those streams: each fact has one owner and every reader names it.

In a design that holds anything but model blocks, a composite holding model blocks
alone -- the ISP inside a board -- is built whole, as one cell: the register-file
top its build generates, wired by the ports that build published, its register map
beside it. A design of model blocks alone is itself what gets built.

The ISP is sensor-independent. What it is built for is the stream entering it
-- the depth its samples arrive at, the widest frame and the tallest -- stated
on its input port, and nowhere else; a sensor whose mode fits them is served.
There is one standard ISP, `revela.pipelines.bayer_isp`, and every design is
built of it -- the pipelines in `revela/pipelines/` and the board's -- rather
than spelling a chain out beside it, which would be a second description of
it free to drift.

A sensor's tunings live with the sensor in `revela/sensors/<name>/`, as a
profile: register values for one block, keyed relative to it, so a profile for
`bayer_isp` tunes every design built of it, at every instance. A profile may
include another by reference, and the value it sets itself wins. It cannot add
a block or reorder the graph, and every value is checked against the built
register map -- name, shape and range -- so a bad number fails at load rather
than on hardware. A design's own values are its reset values; a profile is the
layer on top, and nothing is derived from a sensor description behind it.

A block is found by its reference through the import path, so a checkout and an
installed wheel resolve alike, and a library outside any package is a folder on
that path (`--library`). A declared stub has no `ParamSet` and is not a block --
better to fail at composition than to build a pipeline containing a block that
has no model and cannot be verified.

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
`np2hw.pipeline.compose`, not an architectural rewrite.

## Sensors

`revela/sensors/<name>/sensor.json`, validated against `schema.json` by a pytest
test so bad contributions fail CI, not hardware. The schema is versioned.

Contents: Bayer phase, black level pedestal, bit depth and packing, frame timing
(line length, frame length, line time), the exposure formula (exposure time →
coarse/fine integration registers), the gain code mapping (Sony parts are
typically `256/(256-code)`), and register sequences per mode.

**Where each part is consumed:**

- **Software at runtime** — exposure/gain conversion, mode selection, I2C
  sequences. The sensor package carries the laws to the source, where the 3A
  loops compute the writes from them. This is most of the file.
- **Build time — nothing.** The ISP is built for a stream: the depth its
  samples arrive at, its widest frame and its tallest, stated on its input
  port. A sensor whose mode fits is served; `sensor.json` never feeds a
  build.
- **Generated logic, ONE case** — on a headless target with no CPU, generate an
  I2C init sequencer from the register sequences: a ROM of register writes plus
  the state machine that walks it. A good demonstration of np2hw beyond datapath.

**Prefer run-time facts over build-time generation.** Bayer phase as a run-time
fact -- from the stream's header, or a two-bit register -- costs almost nothing
and lets one bitstream serve every sensor. **Never
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
revela/          (np2hw holds the machinery: np2hw.blocks -- the framework,
                  register declarations, the stream spec; np2hw.design -- the
                  composite format, its loader, netlist and checks, profile
                  layering; np2hw.pipeline -- composition, allocation, the map,
                  the RTL twin, the design pack and `np2hw pipeline`;
                  np2hw.toolchain -- the Vivado flow)
  designs/        bayerlink_display.json, the camera on a display, for any
                  board to fill and feed
  pipelines/      compositions of blocks: bayer_isp.json (the standard ISP),
                  stereo.json (two of it)
  blocks/         one file per block; the decorated model IS the block
                  (its .params is the register set, .run() the reference,
                  .generate() the RTL)
    pipe.py       pipeline context (an ordinary block, at base 0)
    blacklevel.py complete: model + generation + bit-exact test
    whitebalance.py complete; per-CFA gain, fixed-point variants
    gamma.py      complete; PWL tone curve from a knot register array --
                  TWO blocks (gamma.py also reads as rgb_gamma.py to the
                  drift test): single-channel, and per-RGB-channel over one
                  shared knot table via the channels view
    stats.py      model + register map; RTL pending np2hw reductions
    ccm.py        complete; first 3-channel block, seatless until demosaic
    logo.py       complete; a mark composited from a ROM, position derived
                  from the frame's own shape -- the first block whose table
                  is data rather than coefficients, and the first to prove
                  Rom() lands in block RAM
    tofquad.py    complete; time-of-flight phase + amplitude by CORDIC from
                  the last four frames as channels of one word, each routed
                  onto the quadrature by the phase tag it carries
    tofrange.py   complete; phase to perpendicular depth, the lens as
                  registers over the raster's own counters (coords())
    demosaic/
      bilinear.py complete; CFA-position-selected taps on one shared window,
                  three channels out via np.stack -- the model never packs
      bicubic.py  complete; Keys half-phase cubic per lattice, 7x7 window,
                  signed accumulators, floor shifts, clipped overshoot
      hamilton_adams.py  complete; TWO blocks in one file (the drift test
                  reads them as ha_green.py / ha_rb.py): direction-adaptive
                  green with a per-pixel select, then chroma from colour
                  differences over the (raw, green) word -- bayer+g between
      malvar.py   stub; the usual default, gradient-corrected 5x5
      menon.py    stub; directional with refinement, the quality tier
    ...           stubs with intent documented
  sensors/        schema.json + <name>/sensor.json, and a sensor's profile.json
  host/           transports (spi, udp, pynq), register accessors, and the
                  source's packages (bridge_module.py, sensor_module.py)
  boards/         board designs and the blocks they use; np2hw.toolchain
                  renders them for Vivado and holds each described vendor
                  block to what Vivado built;
    common/       blocks any board may use: described once with their
                  implementations (hdmi_tx, clock, frame_dma, stream_dma,
                  stream_cdc),
                  and the modules a generic design needs (isp_entry,
                  tee_shim, isp_axis, the raster's generators)
    zynq7/        the family: the PS (described, its ports by its settings),
                  vendor IP not yet described, the bus, GPIO words
    pynq_z2/      the board: its jacks, its ISP, pynq_demo (bayerlink_display
                  filled and fed by role), build.sh
tests/            pytest + cocotb testbenches
experiments/      exploratory work, NOT part of the library, float allowed
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
- **A block is a decorated function.** np2hw's block framework
  (`np2hw.blocks`) holds what the arithmetic cannot say -- the register set,
  which context bits the model takes -- and `Block.generate()` is generic,
  shared by every block. revela's `@ispblock` is np2hw's decorator with
  revela's pipeline context (`revela.blocks.context`) bound as the vocabulary
  its blocks consume; the Verilog traced from a model carries the licence the
  model's own source file states. Stream WIDTHS are
  deliberately not declared: np2hw derives them from the trace, and declaring
  them twice is how `in_flags` and `ctx_ports` went wrong. A design holds
  Blocks, not modules; the registry discovers Blocks; a block that cannot be
  traced yet says WHY in `not_traceable`.
- **Streams are just math.** A port declares nothing but its name. The channel
  count and field width of every stream are what the driving model's
  arithmetic actually packed -- np2hw publishes them in the traced core's
  interface, and the composer threads each block's traced output into the
  next block's input, starting from the pipeline input, the ONE stream fact a
  design states. There are no meaning tags: whether a connection makes sense
  is the author's judgment, exactly as it is in NumPy. A connection the
  arithmetic cannot digest (a three-channel unpack of a one-channel word)
  dies inside the trace at compose time, before any Verilog exists -- the
  math is the check.
## The revela / np2hw interface

These cross the boundary, and nothing else:

    revela -> np2hw   Param, Image2D, to_ir(model, image, *params)
                      compose(instances, connections, ports)
                      Netlist -- the graph, checked before emission
                      Reg, RegBlock, AddrMap, systemrdl, control_wrap --
                        the register file in front of the composed top
                      np2hw.fusesoc -- the pack's manifest
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
description.** np2hw writes the ports, so np2hw publishes the port list; np2hw
packs the words, so np2hw publishes each traced core's channel count and field
width, and revela reads them instead of keeping its own table.

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
- Next: a **buffered fork** -- a fan-out to two consumers that both apply
  backpressure is refused today rather than emitted as something that
  deadlocks under load -- and statistics RAM windows behind the region the
  allocator already reserves for them.
- `demosaic/bilinear` is complete: the first phase-selected STENCIL block
  (np2hw traces `out[r::2, c::2] = taps[r::2, c::2]` to one shared window
  with a positional tap-combination mux) and the first three-channel
  producer (`np.stack([r, g, b], axis=-1)`; the wire word is the stream
  layer's business). `malvar` and `menon` remain declared stubs.
