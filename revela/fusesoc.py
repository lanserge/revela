# Copyright 2026 Serge Rabyking
# SPDX-License-Identifier: Apache-2.0 WITH SHL-2.1
"""FuseSoC integration: a pipeline description in, a design pack out.

revela's half of the generator conversation is POLICY only: parse the
design JSON, resolve blocks, allocate addresses, and write the artifacts
that speak revela's vocabulary (the register map JSON). Everything a
hardware tool consumes -- the Verilog, the SystemRDL -- is rendered by
np2hw from revela's decisions, exactly as :meth:`Pipeline.generate` and
:meth:`Pipeline.write_systemrdl` already do; and the packaging protocol
itself (the generator input format, the ``.core`` manifest) has ONE
implementation, in :mod:`np2hw.fusesoc`. This file connects the two and
adds nothing of its own.

A consuming core file uses it as:

    generate:
      isp:
        generator: revela
        parameters:
          design: pipelines/mono/imx219/basic/pipeline.json
"""
from __future__ import annotations

import sys
import json
from pathlib import Path


def emit(design, output_dir, name: str | None = None, control: bool = True,
         clock_mhz: float | None = None, verify: bool = True) -> dict:
    """Build a design and write its pack: Verilog, maps, manifest.

    Args:
        design: a description dict, or a path to a pipeline JSON.
        output_dir: where the pack lands. Created if absent.
        name: core VLNV for the manifest; default derives from the
            pipeline's own name.
        control: emit the AXI4-Lite control plane in front of the datapath
            (the shippable form). ``False`` stops at the datapath, which is
            what a bit-exact testbench wants.
        clock_mhz: the clock the core must make. Every pointwise stage is
            depth-checked against it at generation time by arithmetic on
            the traced expression graph, and a too-deep stage is cut into
            pipeline stages there. None generates unchecked -- for a
            simulation-only build, never for one headed at a bitstream.
        verify: run the design's Verilog under Verilator against its own
            NumPy models before writing anything, on a synthetic frame with
            synthetic non-degenerate register values, and REFUSE the pack on
            any mismatch. On by default because this is the last gate
            before a hardware tool: an unverified pipeline should not reach
            a bitstream through packaging. ``False`` is for consumers that
            prove the same thing elsewhere (a packaging test, a flow whose
            own testbench is the twin).

    Returns:
        ``{artifact: Path}`` for everything written -- Verilog, register
        map, SystemRDL, register documentation, ``.core`` manifest --
        plus two facts about the build for whoever instantiates it:
        ``"toplevel"`` (the generated top module's name) and
        ``"latency"`` (its latency in pixels).
    """
    from np2hw.fusesoc import write_core
    from revela import designs
    from revela.compose import register_map_markdown

    pipeline = (designs.build(design) if isinstance(design, dict)
                else designs.load(design))

    if verify:
        # Generation and verification are ONE step: the same composition
        # runs under Verilator against the same block models. The values
        # are synthetic on purpose -- deterministic, nudged off every
        # transparent reset so the arithmetic is exercised, and never
        # anyone's calibration.
        import numpy as np

        from revela import run as runner

        values = runner.synthetic_values(pipeline)
        frame = np.random.default_rng(20260813).integers(
            0, 1 << pipeline.spec.bit_depth,
            size=(pipeline.height, pipeline.width), dtype=np.uint16)
        context = {"bayer_phase": 2}
        chain = runner.pixel_chain(pipeline, None, None)
        model = runner.run_model(chain, frame, values, context,
                                 pipeline.spec.bit_depth)
        rtl = runner.run_rtl(chain, frame, values, context,
                             pipeline.spec.bit_depth)
        if not np.array_equal(model, rtl):
            raise ValueError(
                f"{pipeline.name}: the generated RTL DIFFERS from the "
                "model; refusing to emit an unverified pack")
        print(f"{pipeline.name}: twin bit-exact with the model "
              f"({model.size} words)")

    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    generated = pipeline.generate(
        control=control,
        clk_ns=None if clock_mhz is None else 1000.0 / clock_mhz)
    written = {
        "verilog": output_dir / f"{pipeline.name}.v",
        "regmap": output_dir / f"{pipeline.name}_regmap.json",
    }
    written["verilog"].write_text(generated.verilog)
    pipeline.write_register_map(written["regmap"])
    written["systemrdl"] = pipeline.write_systemrdl(
        output_dir / f"{pipeline.name}.rdl")
    # The documentation is the map in prose, and it ships WITH the map:
    # whoever reads a register in review reads this, and a pack whose
    # docs were rendered by a separate run is a pack whose docs can
    # describe a different build.
    written["docs"] = output_dir / f"{pipeline.name}-registers.md"
    written["docs"].write_text(register_map_markdown(pipeline.register_map()))

    written["core"] = write_core(
        output_dir,
        name or f"lanserge:revela:{pipeline.name}:0",
        {
            "rtl": {"files": [written["verilog"].name],
                    "file_type": "verilogSource"},
            # The maps ride along as data: the register map JSON is
            # revela's own vocabulary for hosts and tests, the SystemRDL is
            # np2hw's rendering for the integrator's register tooling.
            "maps": {"files": [written["regmap"].name,
                               written["systemrdl"].name],
                     "file_type": "user"},
            "docs": {"files": [written["docs"].name],
                     "file_type": "user"},
        },
        toplevel=generated.top,
        description=f"revela design pack for {pipeline.name}",
    )
    written["toplevel"] = generated.top
    # Not a file: the pixel latency through the core, which whoever
    # instantiates it needs and would otherwise re-derive by building
    # the design a second time.
    written["latency"] = generated.latency
    # Also not a file, and the reason this is published at all: the width
    # of the word each output carries is TRACED, so an integrator that
    # writes it down instead of reading it has a second owner of a fact
    # that changes whenever the models do.
    written["boundary"] = generated.meta["boundary"]
    # ...and on disk too, because the integrator here is a shell script
    # and a block design, neither of which can import Python.
    written["build"] = output_dir / f"{pipeline.name}_build.json"
    written["build"].write_text(json.dumps({
        "toplevel": generated.top,
        "latency": generated.latency,
        # What the cores were BUILT around, which is the maximum they can
        # process: the line buffers are this deep and the pointwise cores
        # reframe every line from it. An integrator handed a wider frame
        # must crop before this pack sees it -- the cores receive neither
        # the stream's end-of-line nor the header's width, so they cannot
        # notice, and the failure is a sheared picture with the CFA phase
        # inverted on alternate rows rather than an error.
        "geometry": {"width": pipeline.width, "height": pipeline.height},
        "boundary": generated.meta["boundary"],
    }, indent=2) + "\n")
    return written


def main(argv=None) -> int:
    """revela as a FuseSoC generator.

    Parameters (in the consuming core's ``generate`` section):
        design: path to the pipeline JSON, relative to the calling core.
            Required -- the design is the input, and there is no default
            pipeline.
        control: emit the AXI4-Lite control plane (default true).
        clock_mhz: the clock the core must make; pointwise stages are
            depth-checked and pipelined against it (default: unchecked).
        verify: twin-verify under Verilator before emitting (default
            true; an unverified pipeline should not reach a bitstream).
    """
    from np2hw.fusesoc import read_generator_input

    argv = sys.argv[1:] if argv is None else list(argv)
    if len(argv) != 1:
        raise SystemExit("usage: revela-fusesoc <generator-input.yml>")
    data = read_generator_input(argv[0])
    parameters = data["parameters"]
    if "design" not in parameters:
        raise SystemExit("generator parameter 'design' is required: the "
                         "path to a pipeline JSON, relative to the calling "
                         "core")
    clock = parameters.get("clock_mhz")
    emit(Path(data["files_root"]) / str(parameters["design"]),
         Path.cwd(),
         name=str(data.get("vlnv") or "") or None,
         control=bool(parameters.get("control", True)),
         clock_mhz=None if clock is None else float(clock),
         verify=bool(parameters.get("verify", True)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
