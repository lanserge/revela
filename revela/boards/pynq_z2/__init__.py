# Copyright 2026 Serge Rabyking
# SPDX-License-Identifier: Apache-2.0 WITH SHL-2.1
"""PYNQ-Z2: the revela ISP between a bayerlink receiver and an HDMI raster.

HDMI in carries raw Bayer frames from a camera source; the receiver --
bayerlink_fpga's, behind the front end the hdmi_in primitive names --
unpacks them into one sample per clock; the ISP turns them into RGB; the
raster drives HDMI out. Build it with build.sh beside this file, from the
directory the build should land in (see its header).

The board's primitives are hdmi_in and hdmi_out (one JSON block and one
constraints fragment each); design.json names the ones it uses.
"""
from __future__ import annotations

import json
from pathlib import Path

from revela.boards import resolve

HERE = Path(__file__).resolve().parent


def board() -> dict:
    return json.loads((HERE / "board.json").read_text())


def design() -> dict:
    return json.loads((HERE / "design.json").read_text())


def primitive(name: str) -> dict:
    """One of the design's primitives, by the name the design gives it,
    with the constraints file it names resolved to a path."""
    block, folder = resolve(design()["primitives"][name])
    block = dict(block)
    block["constraints"] = folder / block["constraints"]
    return block


def constraints() -> list[Path]:
    """Every constraints file the design is built with: its own first,
    then each primitive's, in the order design.json names them."""
    d = design()
    return [HERE / d["constraints"],
            *(primitive(name)["constraints"] for name in d["primitives"])]


def isp_ref() -> str:
    """The ISP this design builds, by reference: the isp instance's."""
    isp = next(i for i in design()["instances"] if i["name"] == "isp")
    return isp["design"]


def isp_name() -> str:
    """The ISP's name: its generated modules and pack are named after it."""
    return isp_ref().rpartition(":")[2]


def isp_stream() -> dict:
    """The facts of the stream entering the ISP -- bits, max_width,
    max_height, context -- as the composite states them where it enters."""
    from revela.designs.composite import load
    streams = load(isp_ref()).streams
    if len(streams) != 1:
        raise ValueError(f"{isp_ref()}: this board feeds one stream, and the "
                         f"ISP takes {sorted(streams)}")
    return next(iter(streams.values()))


def hdl_dirs(out: Path) -> list[Path]:
    """Every directory holding this design's Verilog: the board's own
    glue, the receiver's, and what the build generated."""
    import bayerlink_fpga
    return [HERE / "hdl", bayerlink_fpga.HDL, Path(out) / "hdl"]
