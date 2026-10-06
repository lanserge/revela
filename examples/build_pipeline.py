# Copyright 2026 Serge Rabyking
# SPDX-License-Identifier: Apache-2.0 WITH SHL-2.1
"""Read a pipeline: its address map, its topology, its resolved values.

    python examples/build_pipeline.py revela.pipelines.stereo:revela_stereo
    python examples/build_pipeline.py revela.boards.pynq_z2.isp:revela_isp_core

The artifacts come from ``np2hw.pipeline.pack.emit`` -- the same one
emitter ``np2hw pipeline generate`` and the FuseSoC generator call, which proves the RTL
bit-exact against the models before it writes anything. There is no
second way to build a design here, because a second way is a way for two
builds of one design to differ.

What this script adds is the READING: where each block landed, what the
streams look like, what a profile resolves to and which layer each value
came from. That is what a person wants when opening a design somebody
else wrote, and none of it belongs in a pack.

A pipeline is described in JSON and nowhere else. There is one way to say what a
pipeline contains, so a design cannot exist in two forms that disagree -- and a
builder GUI emitting that JSON is on exactly the same footing as a file written
by hand.
"""
from __future__ import annotations

import argparse
from pathlib import Path

from np2hw import pipeline as pipelines
from np2hw.design import profile as profiles
from np2hw.pipeline import compose, pack


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("description",
                        help="a composite, e.g. revela.pipelines.stereo:revela_stereo")
    parser.add_argument("--out", type=Path, default=None,
                        help="output directory (default: build/<pipeline>/)")
    parser.add_argument("--profile", default=None,
                        help="also anchor this profile (package.module:name) "
                             "in the design and report what it sets")
    parser.add_argument("--clock-mhz", type=float, default=None,
                        help="the clock the design must make; every pointwise "
                             "stage is depth-checked and cut against it")
    parser.add_argument("--no-verify", action="store_true",
                        help="skip the Verilator twin: a quick structural "
                             "read, not a pack to build anything from")
    args = parser.parse_args()

    pipeline = pipelines.load(args.description)
    form = compose.shape(pipeline)
    out = args.out or Path("build") / pipeline.name
    written = pack.emit(args.description, out,
                        clock_mhz=args.clock_mhz,
                        verify=not args.no_verify)

    print(f"design      {args.description}")
    print(f"pipeline    {pipeline.name}: "
          + " | ".join(_stream_summary(pipeline)))
    print(f"datapath    {form.spec.bit_depth}-bit, "
          f"{form.width}x{form.height}, latency "
          f"{written['latency']} pixel(s)")
    print()
    print("address map")
    for stage in compose.stages(pipeline):
        windows = ", ".join(f"{name} @ 0x{base:04x}"
                            for name, base in stage.instance.stats_bases.items())
        print(f"  0x{stage.instance.base:04x}  {stage.path:<22} "
              f"{len(stage.paramset):>2} registers"
              + (f"   stats: {windows}" if windows else ""))

    if args.profile is not None:
        profile = profiles.resolve(args.profile)
        anchored = profiles.flatten(profile, compose.register_map(pipeline))
        print()
        print(f"profile     {profile.ref}, for {profile.for_ref}: "
              f"{len(anchored)} register(s)")
        for key, value in anchored.items():
            print(f"  {key:<32} {value}")

    print()
    for artifact in ("verilog", "regmap", "systemrdl", "docs", "core"):
        print(f"wrote {written[artifact]}")
    return 0


def _stream_summary(pipeline) -> list[str]:
    """One entry per independent stream, in datapath order."""
    streams: dict[str, list[str]] = {}
    for stage in compose.datapath(pipeline):
        prefix, _, instance = stage.path.rpartition(".")
        streams.setdefault(prefix, []).append(instance)
    return [f"{name + ': ' if name else ''}" + " -> ".join(blocks)
            for name, blocks in streams.items()]


if __name__ == "__main__":
    raise SystemExit(main())
