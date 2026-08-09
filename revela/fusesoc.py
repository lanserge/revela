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
from pathlib import Path


def emit(design, output_dir, name: str | None = None, control: bool = True) -> dict:
    """Build a design and write its pack: Verilog, maps, manifest.

    Args:
        design: a description dict, or a path to a pipeline JSON.
        output_dir: where the pack lands. Created if absent.
        name: core VLNV for the manifest; default derives from the
            pipeline's own name.
        control: emit the AXI4-Lite control plane in front of the datapath
            (the shippable form). ``False`` stops at the datapath, which is
            what a bit-exact testbench wants.

    Returns:
        ``{artifact: Path}`` for everything written.
    """
    from np2hw.fusesoc import write_core
    from revela import designs

    pipeline = (designs.build(design) if isinstance(design, dict)
                else designs.load(design))
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    generated = pipeline.generate(control=control)
    written = {
        "verilog": output_dir / f"{pipeline.name}.v",
        "regmap": output_dir / f"{pipeline.name}_regmap.json",
    }
    written["verilog"].write_text(generated.verilog)
    pipeline.write_register_map(written["regmap"])
    written["systemrdl"] = pipeline.write_systemrdl(
        output_dir / f"{pipeline.name}.rdl")

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
        },
        toplevel=generated.top,
        description=f"revela design pack for {pipeline.name}",
    )
    return written


def main(argv=None) -> int:
    """revela as a FuseSoC generator.

    Parameters (in the consuming core's ``generate`` section):
        design: path to the pipeline JSON, relative to the calling core.
            Required -- the design is the input, and there is no default
            pipeline.
        control: emit the AXI4-Lite control plane (default true).
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
    emit(Path(data["files_root"]) / str(parameters["design"]),
         Path.cwd(),
         name=str(data.get("vlnv") or "") or None,
         control=bool(parameters.get("control", True)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
