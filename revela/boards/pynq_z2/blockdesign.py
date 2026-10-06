# Copyright 2026 Serge Rabyking
# SPDX-License-Identifier: Apache-2.0 WITH SHL-2.1
"""Write the PYNQ-Z2 demo's block design, from its JSON.

    python3 -m revela.boards.pynq_z2.blockdesign --out <build dir>

The design is revela.boards.pynq_z2.pynq_demo. Loading it builds the ISP
inside it, and the ISP's widths reach everything around it as the design's
own parameters, assigned from the ISP's facts; the raster's pixel clock is
one of its parameters too, read from np2hw's mode table by an expression.
The board says what is possible: whether it can host the capture path,
and how fast each HDMI jack runs. A choice the board cannot honour is
refused here, by name, rather than by a cascade of Tcl errors in Vivado.

Writes <build dir>/generated/bd.tcl, which build.sh hands Vivado, and
gpio_layout.json: the GPIO fields by name, from the same declarations,
for the bench scripts -- which never write a bit position down. Its
checksum is built into the fabric as a constant GPIO word, so a script
handed a layout from another build refuses it instead of misreading.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from revela.boards.pynq_z2 import HERE, board, choices, default
from np2hw.toolchain import vivado
from np2hw.design.composite import block_name, load, write_generated

DESIGN = "revela.boards.pynq_z2.pynq_demo"


def check_board(c: dict) -> None:
    """The design's choices met against the board's facts."""
    from np2hw.video_out import pixel_khz
    b = board()
    if c["capture"] and not b.get("capture", False):
        raise SystemExit("blockdesign: this board cannot host the capture "
                         f"path ({b.get('capture_note', 'see board.json')})")
    tx = default("hdmi_out", "max_pixel_khz") / 1000
    if c["out_khz"] / 1000 > tx:
        raise SystemExit(f"blockdesign: mode {c['mode']} needs "
                         f"{c['out_khz'] / 1000:g} MHz but hdmi_out is built "
                         f"for {tx:g}")
    rx = default("hdmi_in", "max_link_khz") / 1000
    link = pixel_khz(c["link_mode"]) / 1000
    if link > rx:
        raise SystemExit(f"blockdesign: link_mode {c['link_mode']} advertises "
                         f"{link:g} MHz but hdmi_in is constrained to {rx:g}; "
                         "a source honouring it would send a clock this "
                         "receiver cannot lock")


def gpio_layout(design) -> dict:
    """Every GPIO block's fields as software reads them, from the same
    declarations the block design was built from -- only the fields this
    build has, and a constant field with the value its cell holds."""
    from revela.boards.zynq7 import gpio
    top = json.loads((HERE / "pynq_demo.json").read_text())["blocks"]["pynq_demo"]
    by_cell = {path.rpartition(".")[2]: inst
               for path, inst in design.instances.items()}
    out = {}
    for node in top["nodes"].values():
        if node["block"] != "revela.boards.zynq7.gpio:words":
            continue
        config = node["config"]
        # Positions from the whole declaration -- a field this build
        # lacks still holds its bits -- and then only the fields it has.
        layout = gpio.layout(config)
        for ch, declared in zip(layout["channels"], config["channels"]):
            absent = {f["name"] for f in declared["fields"]
                      if "name" in f and "cell" in f
                      and f["cell"] not in by_cell}
            ch["fields"] = {k: v for k, v in ch["fields"].items()
                            if k not in absent}
            for f in declared["fields"]:
                if "value" in f and f["name"] in ch["fields"]:
                    ch["fields"][f["name"]]["value"] = \
                        by_cell[f["cell"]].build["CONST_VAL"]
        out[config["gpio"]] = layout
    return out


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="python3 -m revela.boards.pynq_z2.blockdesign")
    parser.add_argument("--out", required=True, type=Path)
    args = parser.parse_args(argv)
    out = args.out.resolve()
    check_board(choices())
    # The layout's checksum is built into the fabric, where software reads
    # it back; the layout does not depend on it, so a first load finds it.
    from revela.boards.zynq7.gpio import digest
    layout_id = digest(gpio_layout(load(DESIGN)))
    design = load(DESIGN, build={"layout_id": layout_id})
    layout = gpio_layout(design)
    if digest(layout) != layout_id:
        raise SystemExit("blockdesign: the GPIO layout moved when its own "
                         "checksum was built in")
    paths = {}
    for line in (out / "generated" / "paths.tcl").read_text().splitlines():
        if line.startswith("set "):
            _, name, value = line.split(None, 2)
            paths[name] = value.strip("{}")
    # The generated leaves' Verilog, made by their generators from this
    # design's settings, beside the rest of the build's.
    write_generated(design, out / "hdl")
    b = board()
    tcl = vivado.emit(design, out=out, part=b["part"],
                      board_part=b["board_part"],
                      board_files=Path(paths["p_board_files"]),
                      ip_repos=[Path(paths["p_vivado_library"]),
                                out / "generated" / "busdef"],
                      # the project and its block design, by the name the
                      # board's flow scripts and bench files read
                      name="rx")
    target = out / "generated" / "bd.tcl"
    target.write_text(tcl)
    # what each described vendor block should be once the tool has built
    # it, for np2hw.toolchain.vivado.vendor_check after the block design runs
    from np2hw.toolchain.vivado.vendor_check import expected
    (out / "generated" / "vendor_expected.json").write_text(json.dumps(
        expected(design, vivado.cell_names(design)), indent=1) + "\n")
    # the build's product, for any tool that reads it
    (out / "generated" / "netlist.json").write_text(
        json.dumps(design.netlist, indent=1, default=str) + "\n")
    (out / "generated" / "gpio_layout.json").write_text(
        json.dumps(layout, indent=1) + "\n")
    from np2hw.ipxact import write_definitions
    write_definitions(out / "generated" / "busdef")
    p = design.parameters
    print(f"generated {target} -- {len(design.instances)} cells, "
          f"{p['sample_bits']}-bit into the ISP, {p['isp_out_bits']}-bit "
          f"out, {p['out_khz'] / 1000:g} MHz raster"
          + (", genlocked" if design.parameters["genlock"] else "")
          + (", capture" if design.parameters["capture"] else "")
          + f"; GPIO layout 0x{layout_id:08X}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
