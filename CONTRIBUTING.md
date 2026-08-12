# Contributing to revela

Thank you for considering it. Please read this before opening a pull request —
in particular the CLA section, which is not optional, and the licence
provenance rules, which will get a contribution rejected on sight if missed.

## Contributor Licence Agreement

**Every contributor must sign the [CLA](CLA.md) before a pull request is merged.**

Why: revela is released openly under the Solderpad Hardware License v2.1, and a
**commercial licence with support and indemnity is offered alongside it** — which
means licensing the whole work, contributions included, on terms other than the
open ones. The CLA is what makes that possible, by granting the maintainer a
sublicensable and transferable licence rather than leaving each file's terms
fixed by whoever wrote it.

**It is a licence, not an assignment. You keep your copyright.** You also keep
the right to use your own contribution for anything you like. In exchange:

- Your contribution stays available under the Solderpad licence — releasing it
  openly is not something a later commercial tier takes back.
- You are credited in the commit history and in `AUTHORS`.

Read [CLA.md](CLA.md) for the actual terms; it is marked as a draft pending legal
review, and section 5 explains the sublicense-and-transfer wording in plain
words. To sign: open your first pull request, and the CLA assistant will comment
with a link. One signature covers all future contributions.

If you cannot sign — many employment contracts make it complicated — please say
so in the pull request. Small fixes can often be taken as suggestions and
reimplemented, and a bug report with a failing test case is enormously valuable
and needs no CLA at all.

## Licence headers

Every `.py` file under `revela/` must begin with exactly:

```python
# Copyright <year> <author>
# SPDX-License-Identifier: Apache-2.0 WITH SHL-2.1
```

`tests/test_spdx_headers.py` fails the build if one is missing. It is checked
mechanically because a licence header that is applied inconsistently is worse
than useless in a review.

## The four rules

Read [docs/design-rules.md](docs/design-rules.md) in full before changing
anything under `revela/`. The
four rules are the design, not preferences, and a pull request that breaks one
will be asked to change regardless of how well it works. In brief:

1. **One model per block**, written at the hardware's arithmetic. No float
   reference model, no float-versus-fixed comparison anywhere in the library.
   `np.float64` in `revela/blocks/` is a review failure.
2. **Parameters declared once, per block, local offsets only.** Blocks never see
   absolute addresses.
3. **Bit-exact verification** of model against generated Verilog. Not "close".
4. **Generated Verilog must be readable.** It is a product surface.

`experiments/` is exempt from all of this. It may use float freely, it is where
algorithm exploration belongs, and nothing in `revela/` may import from it.

## Contributing a sensor description

`revela/sensors/<name>/sensor.json`, validated against `schema.json` by
`tests/test_sensors.py`.

**Do not copy register tables out of the Linux kernel media drivers.**
`drivers/media/i2c/imx219.c` and its siblings are GPL-2.0. That licence is
incompatible with this project's, and transcribing their register tables here
would make revela undistributable under its stated terms. Derive values from the
datasheet, or reference the driver in `provenance.notes` without copying from it.
The schema has a `provenance.gpl_driver_transcribed` field that must be `false`;
it exists so the question is answered explicitly rather than left ambiguous.

**Do not commit per-unit calibration.** A CCM, a lens shading mesh and a defect
map are properties of one physical camera, produced by calibrating it. They vary
per unit, per lens and per illuminant. `sensor.json` holds what is true of
*every* part of that model; calibration outputs are loaded at runtime from
wherever your calibration process writes them. The schema rejects a `calibration`
key outright.

**Prefer runtime registers to build-time generation.** Bayer phase as a two-bit
register costs almost nothing and lets one bitstream serve every sensor. Never
generate a different pipeline per sensor: it turns the verification matrix into
sensors × modes, and a matrix like that does not stay green.

## Contributing a block

**Algorithm blocks are maintainer-implemented.** Anything with an academic
or patent lineage -- demosaic, denoise, sharpening, tone mapping, the
methods with names on them -- is written here by the maintainer, from the
paper or the patent text, clean-room. This is not about trust in anyone's
skill: revela is offered commercially with indemnity, and that promise
rests on a single, documented provenance trail for every method in the
tree. A pull request cannot carry its author's reading history with it.
Every algorithm block states when its patent situation was checked
(`patent-checked YYYY-MM-DD` in the module docstring -- CI enforces it),
and that statement has to be the implementer's own.

What is enormously welcome for algorithm blocks instead: pointers to
methods worth implementing (with the paper), patent-status research,
failing test cases, and quality comparisons. Open an issue -- the
`sponsorable` label is exactly these, priced.

Blocks WITHOUT such lineage -- plumbing, statistics, format conversions --
follow the normal path below, CLA and all.

1. One file in `revela/blocks/`, one `@ispblock`-decorated model function (the decorated function IS the block: `Block.run()` runs it, the generic `generate()` traces it, `.params` is its register set — declare nothing beside it).
2. The model is the specification. Write it at the hardware's arithmetic first
   and let the RTL follow, not the other way round.
3. A test in `tests/` proving bit-exact agreement between model and generated
   Verilog under Verilator, driven by cocotb, including under randomised
   backpressure. Framing flags (`sof`/`eol`/`last`) are part of what must be
   exact — a block with correct pixels and wrong `eol` breaks everything
   downstream.
4. Every `Param` needs a `description`. It is carried into the generated Verilog
   comment, the JSON register map and the documentation, so write it for somebody
   reading the RTL in review who has never seen the Python.

If np2hw cannot express what your block needs, **that is a bug report or feature
request for np2hw**, raised as a generic capability at the NumPy level — not a
hand-written Verilog workaround in revela. Point at the block that needs it.

## Running the tests

```bash
pip install -e ".[dev]"
pytest                       # everything
pytest -m verilog            # only the bit-exact model-vs-RTL tests
pytest -m "not verilog"      # skip them (no Verilator needed)
```

CI installs Verilator and runs the whole suite, including the bit-exact tests,
the schema validation and the SPDX header check. **CI being green is the
project's central claim** — "clone it and reproduce the result" — so a pull
request that leaves it red will not be merged, and a pull request that makes a
test pass by weakening it will be sent back.

## Reporting bugs

A failing test case is worth ten paragraphs of description. For a bit-exactness
failure, the most useful report is the pixel index, the model's value, the RTL's
value, and the parameter values in force — the test helpers print all four on
failure, so pasting that output is usually enough.
