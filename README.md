# revela

**An image signal processing pipeline written in NumPy, from which synthesisable
Verilog is generated.**

An ISP turns what a camera sensor actually produces — a single-colour-per-pixel
mosaic sitting on a pedestal, with the sensor's noise, its dead pixels and its
lens's shading baked in — into an image. revela is that pipeline, written as
NumPy, at the arithmetic the hardware will really use, and compiled to RTL.

```python
def kernel(pixel, offset, bit_depth):
    """One pixel path: add the signed offset, saturate to the datapath range."""
    return (pixel.astype(np.int32) + offset).clip(0, (1 << bit_depth) - 1)
```

That is not a model of the hardware. It **is** the hardware: it is traced into
Verilog, and it is the reference the Verilog is checked against, bit for bit,
under Verilator. There is exactly one of it.

## What makes this different

Most ISP projects have two of everything: a floating-point reference model that
describes the intent, and an RTL implementation that approximates it. Then they
spend their lives arguing about the difference between the two and calling it
"quantisation error". It isn't. The fixed-point algorithm is a *different
algorithm* — different rounding, different saturation, different LUT breakpoints
— and measuring its distance from a float algorithm measures nothing useful.

revela has one model per block, written directly at the hardware's arithmetic:
integer dtypes with explicit widths, explicit rounding, explicit saturation, LUTs
where the hardware will have LUTs, shifts where the hardware will shift. **No
`np.float64` appears anywhere in `revela/blocks/`.** That model is the
specification, the golden reference, and the thing the RTL is generated from.

Image quality is measured where it means something — at pipeline level, against
reference images. Never per block.

## Relationship to np2hw

[**np2hw**](https://github.com/lanserge/np2hw) is the generator: a compiler that
traces NumPy into a streaming line-based IR and emits synthesisable Verilog, with
line buffers, shift registers, edge handling and config registers built for you.

**revela is the ISP, and np2hw's flagship proof case.** The two are separate
projects and revela *depends* on np2hw — it does not vendor or fork it. That
separation is load-bearing in both directions: np2hw stays a general NumPy→RTL
compiler rather than growing ISP-shaped special cases, and revela gets to be a
real ISP rather than a compiler test suite. When revela needs something np2hw
cannot express, the fix goes into np2hw *as a generic capability at the NumPy
level* — never as a revela-side workaround.

**revela emits no Verilog.** Block datapaths are traced from the NumPy models by
np2hw; the top level is built by `np2hw.compose()`, which instantiates the
generated cores and wires the netlist; the AXI4-Lite register file in front of it
is emitted by `np2hw.control_wrap()`. revela supplies the models, the register
declarations and their descriptions, the design JSON, and the software — the
register map with per-instance address allocation, sensor descriptions, the 3A
control loops and the host API.

The split at the control plane is the same one: **revela decides where every
register lives, np2hw decides what an AXI4-Lite slave looks like.** Neither holds
a copy of the other's half, which is why the emitted JSON register map and the
emitted decode cannot drift apart — a test reads the addresses back out of the
generated Verilog and compares them with the map.

## Architecture in one diagram

```
sensor ─▶ [ blacklevel ] ─▶ [ lsc ] ─▶ [ demosaic ] ─▶ [ ccm ] ─▶ [ gamma ] ─▶ out
               │                │                          │
               ╰────────────────┴──── stats ───▶ AE / AWB (host, frame rate)

  pipe @ 0x0000 ── width, height, window, bayer_phase, bit_depth ──▶ (wires to all)
```

Blocks are chained by a direct `valid/ready` stream — no bus between stages, no
DRAM round-trip. One pixel per clock, and backpressure composes all the way back
to the sensor interface. AXI4-Stream Video appears only as an adapter at the
pipeline boundary, where you are talking to somebody else's IP.

## Quick start

```bash
git clone https://github.com/lanserge/revela
cd revela
python -m venv .venv && . .venv/bin/activate
pip install -e ".[dev]"          # np2hw arrives from PyPI
pytest                            # includes the bit-exact model-vs-RTL tests
```

Verilator must be on the path — `apt install verilator` or `brew install
verilator`. The bit-exact tests skip without it; everything else still runs.

A pipeline is described in JSON and nowhere else — a **netlist** of named block
instances and the connections between their ports:

```json
{
  "name": "revela_mono_imx219_basic",
  "sensor": { "name": "imx219", "mode": "binned_2x2" },
  "inputs":  [{ "name": "sensor" }],
  "outputs": [{ "name": "video" }],
  "nodes": [
    { "instance": "blacklevel",   "block": "blacklevel" },
    { "instance": "whitebalance", "block": "whitebalance" },
    { "instance": "gamma",        "block": "gamma" },
    { "instance": "stats",        "block": "stats" }
  ],
  "connections": [
    { "from": "sensor",           "to": "blacklevel.in" },
    { "from": "blacklevel.out",   "to": "whitebalance.in" },
    { "from": "whitebalance.out", "to": "gamma.in" },
    { "from": "gamma.out",        "to": "video" },
    { "from": "blacklevel.out",   "to": "stats.in" }
  ]
}
```

A netlist rather than a list, because a real ISP is not a chain. Note the last
connection: `stats` **taps** the datapath — deliberately PRE-whitebalance,
because AWB must meter the illuminant's cast, and a loop that meters its own
correction chases its tail. The tap consumes the stream and produces no
pixels, so the image path runs `blacklevel → whitebalance → gamma` to the
output. A list of blocks cannot say either of those things — which is the
error the format is shaped to prevent.

A node may also override a declared register's build-time attributes —
`"registers": { "gain": { "frac": 12 } }` — values only, never addresses; the
model, the RTL, the register map and the host all follow the one overridden
declaration.

```bash
python examples/build_pipeline.py pipelines/mono/imx219/basic/pipeline.json
```

Designs live in `pipelines/<topology>/<sensor>/<variant>/`, holding the netlist,
every tuning for it, and a git-ignored `build/`:

```
pipelines/mono/imx219/basic/
  pipeline.json                structure  — nodes and connections
  profiles/indoor.json         values     — registers + 3A tuning
  profiles/outdoor.json        values     — same structure, different numbers
  build/                       generated  — .v, register map, register docs
```

A **profile** carries values and names the sensor it was tuned for, so one
pipeline serves several sensors and scenes. It cannot add a block or rewire the
graph. Values layer: block reset → sensor-derived → profile, and the resolved
settings record which layer each value came from.

**Inputs carry no addresses.** The netlist and the profile are *inputs*, and both
schemas reject an `addresses` key outright. The register map is the *output*.
That is what stops a builder GUI from emitting a map that disagrees with the
hardware it generated — and a test proves a described pipeline rebuilds to
byte-identical Verilog and addresses, so allocation is a pure function of
structure.

The register map JSON is the authoritative address map. The host API and the
register-map documentation are both generated from it; nothing anywhere
hardcodes an address.

## The rules this project is built on

Four, and they are not style preferences — they are why the thing works. They are
stated in full in [docs/design-rules.md](docs/design-rules.md).

1. **One model per block**, at the hardware's arithmetic. No float reference, no
   float-versus-fixed comparison.
2. **Parameters are declared once, per block, and allocated globally.** Blocks
   declare local offsets; the pipeline assigns each block *instance* a base
   address. A stereo pipeline instantiates every block twice, and every block is
   independently testable — both fall out of that, and neither works without it.
3. **Verification is bit-exact agreement** between the NumPy model and the
   generated Verilog under Verilator via cocotb. Identical, not "close enough".
4. **Generated Verilog must be readable.** Meaningful signal names, module names
   matching the block, and each parameter's description carried through from its
   declaration. Somebody licensing this will read the output in review and hand
   it to their own verification team. That is a product requirement.

## Status

`blacklevel`, `whitebalance`, `gamma` and `ccm` are complete end to
end — model, generated Verilog, bit-exact cocotb tests, fixed-point variants
through build-time overrides — and contain **no hand-written Verilog**: each
block, phase mux and LUT included, is one np2hw trace of its NumPy model.
(`ccm` awaits a demosaic to feed it and sits in no example design yet.)
`pipe`, `stats`, the register map with its AXI4-Lite control plane, address
allocation, build-time register overrides, the sensor schema and the
composition layer are implemented. The remaining blocks (`lsc`, `defect`,
`demosaic`, `rgb2yuv`, `sharpen`) are declared stubs, each with its intent
documented in its own file.

`stats` has a model and a register map but no generated RTL: accumulation over a
region is a reduction, which np2hw does not trace yet. It is the one block that
does not currently satisfy rule 3, and that is stated rather than papered over
with a test that only exercises the NumPy side.

## Licence

**Solderpad Hardware License v2.1** — see [LICENSE](LICENSE). SHL-2.1 is a
wraparound over Apache 2.0, so the Apache text is included as
[LICENSE-APACHE](LICENSE-APACHE), with a [NOTICE](NOTICE) file. Every source file
carries:

```
# SPDX-License-Identifier: Apache-2.0 WITH SHL-2.1
```

Solderpad is the right licence here because Apache 2.0 was written for software
and its terms do not cleanly describe what happens when a design is *fabricated*.
SHL-2.1 extends the definitions to cover hardware: "Object form" includes FPGA
bitstreams, mask works and physical instantiation, and "Rights" covers design
right and semiconductor topography rights, not just copyright. You may, at your
option, treat any work released under it as released under Apache 2.0.

Verilog generated by revela carries the same licence, because its datapath is
derived from the models; generated files say so in their header. The structure
np2hw emits around it comes with no obligations at all — see
[docs/LICENSING.md](docs/LICENSING.md).

**A commercial licence, with support and indemnity, is available on request.**

What licence applies to the Verilog revela generates, what you owe by shipping
it, and what the commercial licence adds: **[docs/LICENSING.md](docs/LICENSING.md)**.
Every dependency and its licence: [docs/THIRD-PARTY.md](docs/THIRD-PARTY.md).

## Contributing

Contributions require a signed [CLA](CLA.md) before a pull request is merged —
see [CONTRIBUTING.md](CONTRIBUTING.md). It is a licence grant, not an assignment:
you keep your copyright. Two things that will get a sensor contribution rejected
on sight: register tables transcribed out of the Linux kernel media drivers (they
are GPL-2.0 and incompatible with this licence), and per-unit calibration data
committed as if it described the sensor model.

## Funding

Developed independently. Detail in [FUNDING.md](FUNDING.md); enquiries to
**s.rabykin@gmail.com**.

- **Commercial licence** — patent grant, indemnity, support SLA, and access to
  advanced blocks and verification collateral.
- **Sponsor a block** — fund a declared stub (`lsc`, `defect`, `demosaic`,
  `rgb2yuv`, `sharpen`) or a documented gap; done means bit-exact under the four
  rules, and the work lands here openly, immediately.
- **Sponsor a sensor** — fund characterisation of a sensor you need; you pick it
  and the conditions, get the data first, and it is then published here for all.
- **Consulting** — ISP bring-up, tuning, custom blocks.
- **[GitHub Sponsors](https://github.com/sponsors/lanserge)** — recurring support.

---

revela™ is a trademark of Serge Rabyking. The licence does not grant rights to
the name — see [TRADEMARK.md](TRADEMARK.md).
