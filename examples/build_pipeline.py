# Copyright 2026 Serge Rabyking
# SPDX-License-Identifier: Apache-2.0 WITH SHL-2.1
"""Build a pipeline: emit the Verilog, the register map, and the documentation.

    python examples/build_pipeline.py pipelines/mono/imx219/basic/pipeline.json
    python examples/build_pipeline.py pipelines/stereo/imx219/basic/pipeline.json

A pipeline is described in JSON and nowhere else. There is one way to say what a
pipeline contains, so a design cannot exist in two forms that disagree -- and a
builder GUI emitting that JSON is on exactly the same footing as a file written
by hand.

All three outputs come from the same declarations: the Verilog and the register
map from the block ParamSets, the Markdown from the register map. There is no
fourth place where an address is written down, which is the point.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from revela import designs, profiles, sensors
from revela.compose import register_map_markdown


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("description", type=Path,
                        help="pipeline description JSON, e.g. pipelines/mono/imx219/basic/pipeline.json")
    parser.add_argument("--out", type=Path, default=None,
                        help="output directory (default: <design>/build/)")
    parser.add_argument("--profile", type=Path, default=None,
                        help="also resolve this profile and report its settings")
    args = parser.parse_args()

    pipeline = designs.load(args.description)
    out = args.out or args.description.parent / "build"
    out.mkdir(parents=True, exist_ok=True)

    generated = pipeline.generate()
    verilog_path = out / f"{pipeline.name}.v"
    verilog_path.write_text(generated.verilog)
    map_path = pipeline.write_register_map(out / f"{pipeline.name}.json")
    rdl_path = pipeline.write_systemrdl(out / f"{pipeline.name}.rdl")
    docs_path = out / f"{pipeline.name}-registers.md"
    docs_path.write_text(register_map_markdown(pipeline.register_map()))

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
          f"{pipeline.width}x{pipeline.height}, latency {generated.latency} pixel(s)")
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
    print(f"wrote {verilog_path}")
    print(f"wrote {map_path}")
    print(f"wrote {rdl_path}")
    print(f"wrote {docs_path}")
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
