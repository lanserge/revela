# Copyright 2026 Serge Rabyking
# SPDX-License-Identifier: Apache-2.0 WITH SHL-2.1
"""Read a pipeline: its address map, its topology, its resolved values.

    python examples/build_pipeline.py pipelines/mono/imx219/basic/pipeline.json
    python examples/build_pipeline.py pipelines/stereo/imx219/basic/pipeline.json

The artifacts come from ``revela.fusesoc.emit`` -- the same one emitter
``revela generate`` and the FuseSoC generator call, which proves the RTL
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
import json
from pathlib import Path

from revela import designs, fusesoc, profiles, sensors


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("description", type=Path,
                        help="pipeline description JSON, e.g. pipelines/mono/imx219/basic/pipeline.json")
    parser.add_argument("--out", type=Path, default=None,
                        help="output directory (default: <design>/build/)")
    parser.add_argument("--profile", type=Path, default=None,
                        help="also resolve this profile and report its settings")
    parser.add_argument("--clock-mhz", type=float, default=None,
                        help="the clock the design must make; every pointwise "
                             "stage is depth-checked and cut against it")
    parser.add_argument("--no-verify", action="store_true",
                        help="skip the Verilator twin: a quick structural "
                             "read, not a pack to build anything from")
    args = parser.parse_args()

    out = args.out or args.description.parent / "build"
    written = fusesoc.emit(args.description, out,
                           clock_mhz=args.clock_mhz,
                           verify=not args.no_verify)
    pipeline = designs.load(args.description)

    declared = json.loads(args.description.read_text()).get("sensor")
    if declared:
        sensor = sensors.load(declared["name"])
        mode = sensors.mode(sensor, declared.get("mode"))
        print(f"sensor      {sensor['name']} ({sensor['vendor']}), mode "
              f"{mode['name']} {mode['width']}x{mode['height']}, "
              f"CFA {sensor['cfa']['order']}")
    print(f"design      {args.description}")
    print(f"pipeline    {pipeline.name}: "
          + " | ".join(_stream_summary(pipeline)))
    print(f"datapath    {pipeline.spec.bit_depth}-bit, "
          f"{pipeline.width}x{pipeline.height}, latency "
          f"{written['latency']} pixel(s)")
    print()
    print("address map")
    for stage in pipeline.stages:
        windows = ", ".join(f"{name} @ 0x{base:04x}"
                            for name, base in stage.instance.stats_bases.items())
        print(f"  0x{stage.instance.base:04x}  {stage.path:<22} "
              f"{len(stage.paramset):>2} registers"
              + (f"   stats: {windows}" if windows else ""))

    if args.profile is not None:
        profile = profiles.load(args.profile)
        settings = profiles.resolve(profile, pipeline)
        print()
        print(settings.summary())
        print(f"  control: {settings.control}")

    print()
    for artifact in ("verilog", "regmap", "systemrdl", "docs", "core"):
        print(f"wrote {written[artifact]}")
    return 0


def _stream_summary(pipeline) -> list[str]:
    """One entry per independent stream, in datapath order."""
    streams: dict[str, list[str]] = {}
    for stage in pipeline.datapath:
        prefix, _, instance = stage.path.rpartition(".")
        streams.setdefault(prefix, []).append(instance)
    return [f"{name + ': ' if name else ''}" + " -> ".join(blocks)
            for name, blocks in streams.items()]


if __name__ == "__main__":
    raise SystemExit(main())
