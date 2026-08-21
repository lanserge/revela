# Copyright 2026 Serge Rabyking
# SPDX-License-Identifier: Apache-2.0 WITH SHL-2.1
"""The ``revela`` command.

    revela run pipeline.json in.npy out.png
    revela run pipeline.json --profile indoor.json --to ccm.out in.npy mid.npy
    revela run pipeline.json --from rgb_gamma.in mid.npy out.png
    revela run pipeline.json --rtl in.npy out.npy
    revela generate pipeline.json --out build --clock-mhz 155

``run`` treats a design as a library of image functions: the whole
pipeline by default, or any consecutive run of blocks between ``--from``
and ``--to``. Splitting a run at a port and feeding the intermediate back
in is exact by construction -- both halves together are the same block
models in the same order -- and there is a test that says so.

``generate`` is the same design headed the other way: build it, prove it
bit-exact against its own models under Verilator, and emit the pack a
hardware flow consumes (Verilog, register map, SystemRDL, FuseSoC core).
It refuses to emit anything unverified.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        prog="revela",
        description="Compose, run and generate ISP pipelines.")
    commands = parser.add_subparsers(dest="command", required=True)

    run = commands.add_parser(
        "run",
        help="run a pipeline (or a sub-chain of it) over an image file",
        description="Run a design's NumPy models -- or, with --rtl, its "
                    "generated Verilog under Verilator -- over one frame.")
    run.add_argument("design", help="pipeline description (pipeline.json)")
    run.add_argument("input", help=".npy (raw integers) or .png (8-bit view)")
    run.add_argument("output", help=".npy or .png, by extension")
    run.add_argument("--profile", metavar="PROFILE.json",
                     help="tuning values; must name this same design")
    run.add_argument("--from", dest="inject", metavar="INSTANCE.PORT",
                     help="inject the input at this port "
                          "(default: the pipeline input)")
    run.add_argument("--to", dest="extract", metavar="INSTANCE.PORT",
                     help="extract the output at this port "
                          "(default: the pipeline output)")
    run.add_argument("--set", dest="sets", action="append", default=[],
                     metavar="INSTANCE.REG=VALUE",
                     help="override one register (raw integer; repeatable)")
    run.add_argument("--bayer-phase", type=int, choices=range(4),
                     metavar="0..3",
                     help="override the CFA phase at the injection point")
    run.add_argument("--rtl", action="store_true",
                     help="run the generated Verilog under Verilator and "
                          "verify it against the model word for word")
    run.add_argument("--explain", action="store_true",
                     help="print where every register value came from")
    run.set_defaults(handler=_run)

    generate = commands.add_parser(
        "generate",
        help="build a design, verify it, and emit its pack",
        description="Build a design, prove it bit-exact against its own "
                    "NumPy models under Verilator, and emit the pack: "
                    "Verilog, register map, SystemRDL, FuseSoC core "
                    "manifest. Refuses to emit anything unverified.")
    generate.add_argument("design", help="pipeline description (pipeline.json)")
    generate.add_argument("--out", default=".",
                          help="directory the pack lands in (default: here)")
    generate.add_argument("--clock-mhz", type=float, default=None,
                          help="the clock the core must make; every "
                               "pointwise stage is depth-checked against it "
                               "and a too-deep stage is cut into pipeline "
                               "stages, by arithmetic on the traced "
                               "expression graph (default: unchecked)")
    generate.add_argument("--no-control", action="store_true",
                          help="stop at the datapath: no AXI4-Lite register "
                               "file, coefficients as flat input ports -- "
                               "the testbench form")
    generate.add_argument("--no-verify", action="store_true",
                          help="skip the Verilator twin -- only for flows "
                               "that prove the same thing elsewhere")
    generate.set_defaults(handler=_generate)

    arguments = parser.parse_args(argv)
    try:
        return arguments.handler(arguments)
    except (ValueError, KeyError, TypeError, FileNotFoundError) as error:
        message = error.args[0] if error.args else error
        print(f"revela {arguments.command}: {message}", file=sys.stderr)
        return 2


def _run(arguments) -> int:
    import numpy as np

    from revela import designs, profiles
    from revela import run as runner

    description = json.loads(Path(arguments.design).read_text())
    designs.validate(description)
    pipeline = designs.build(description, validate_description=False)

    profile = None
    if arguments.profile:
        profile = profiles.load(arguments.profile)
        named = profile.get("pipeline")
        if named and Path(named).resolve() != Path(arguments.design).resolve():
            raise ValueError(
                f"profile {profile['name']!r} is tuning for {named}, not for "
                f"{arguments.design} -- refusing to mix them")

    chain = runner.pixel_chain(pipeline, arguments.inject, arguments.extract)
    values, origin = runner.resolve_values(
        pipeline, description, profile, runner.parse_sets(arguments.sets))

    context = dict(values.get("pipe", {}))
    if arguments.bayer_phase is not None:
        context["bayer_phase"] = arguments.bayer_phase

    bit_depth = pipeline.spec.bit_depth
    frame = runner.read_frame(arguments.input, bit_depth)

    if arguments.explain:
        print(runner.explain(chain, values, origin))

    result = runner.run_model(chain, frame, values, context, bit_depth)

    if arguments.rtl:
        rtl = runner.run_rtl(chain, frame, values, context, bit_depth)
        total = result.size // (result.shape[-1] if result.ndim == 3 else 1)
        if not np.array_equal(rtl, result):
            bad = np.argwhere(~np.all(rtl == result, axis=-1)
                              if result.ndim == 3 else (rtl != result))[:5]
            where = ", ".join(f"({y},{x})" for y, x in bad)
            print(f"rtl: DIFFERS from the model, first at {where}",
                  file=sys.stderr)
            runner.write_frame(arguments.output, rtl, bit_depth)
            return 1
        print(f"rtl: bit-exact with the model ({total} words)")
        result = rtl

    runner.write_frame(arguments.output, result, bit_depth)
    return 0


def _generate(arguments) -> int:
    from revela import fusesoc

    written = fusesoc.emit(arguments.design, arguments.out,
                           control=not arguments.no_control,
                           clock_mhz=arguments.clock_mhz,
                           verify=not arguments.no_verify)
    if arguments.clock_mhz is not None:
        print(f"timing: every pointwise stage fits "
              f"{1000.0 / arguments.clock_mhz:.1f} ns "
              f"({arguments.clock_mhz:g} MHz), by the traced depth model")
    for artifact in ("verilog", "regmap", "systemrdl", "core"):
        print(f"{artifact}: {written[artifact]}")
    print(f"toplevel: {written['toplevel']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
