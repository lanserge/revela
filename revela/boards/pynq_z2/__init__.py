# Copyright 2026 Serge Rabyking
# SPDX-License-Identifier: Apache-2.0 WITH SHL-2.1
"""PYNQ-Z2: the revela ISP between a bayerlink receiver and an HDMI raster.

HDMI in carries raw Bayer frames from a camera source; the receiver --
bayerlink_fpga's, behind the front end the hdmi_in jack builds --
unpacks them into one sample per clock; the ISP turns them into RGB; the
raster drives HDMI out. Build it with build.sh beside this file, from the
directory the build should land in (see its header).

The board's library: its jacks, hdmi_in and hdmi_out -- composites that own
their cells, pins and constraints -- and the design built from them,
pynq_demo, with the board glue it uses (modules.json). The family layer
under it is revela.boards.zynq7.
"""
from __future__ import annotations

import json
from pathlib import Path


HERE = Path(__file__).resolve().parent


def board() -> dict:
    return json.loads((HERE / "board.json").read_text())


def default(block: str, parameter: str):
    """A board block's own value for one of its parameters -- the jack's
    limit, the cable's width -- as the block states it."""
    from np2hw.design.composite import evaluate, parse
    from np2hw.design.composite import find
    raw = find(f"revela.boards.pynq_z2.{block}").obj["parameters"][
        parameter]["default"]
    if not isinstance(raw, str):
        return raw
    return evaluate(parse(raw, f"{block}.{parameter}"), lambda name: None)


def variant(out) -> dict:
    """The build's settings of the design's parameters -- a variant, as
    build.sh recorded it beside the build (``generated/build.json``): {}
    for the design as it stands."""
    path = Path(out) / "generated" / "build.json"
    return json.loads(path.read_text()) if path.exists() else {}


def choices(build: dict | None = None) -> dict:
    """The demo's own choices -- its parameters that the build does not
    supply -- as the design states them, with a variant's settings."""
    from np2hw.design.composite import load
    # what the ISP's facts would say, which only building it can answer
    placeholder = {"sample_bits": 0, "isp_out_bits": 0,
                   "fabric.sample_bits": 0, "fabric.isp_width": 0,
                   "fabric.isp_out_bits": 0}
    params = load("revela.boards.pynq_z2.pynq_demo",
                  build={**(build or {}), **placeholder},
                  generate=False).parameters
    return {k: v for k, v in params.items()
            if k not in placeholder}


def isp_ref(build: dict | None = None) -> str:
    """The ISP this design builds, by reference."""
    return choices(build)["pipeline"]


def isp_name(build: dict | None = None) -> str:
    """The ISP's name: its generated modules and pack are named after it."""
    from np2hw.design.composite import block_name
    return block_name(isp_ref(build))


def isp_stream(build: dict | None = None) -> dict:
    """The facts of the stream entering the ISP -- bits, max_width,
    max_height, context -- as the composite states them where it enters."""
    from np2hw.design.composite import load
    streams = load(isp_ref(build)).streams
    if len(streams) != 1:
        raise ValueError(f"{isp_ref(build)}: this board feeds one stream, and the "
                         f"ISP takes {sorted(streams)}")
    return next(iter(streams.values()))
