# Copyright 2026 Serge Rabyking
# SPDX-License-Identifier: Apache-2.0 WITH SHL-2.1
"""Write the PYNQ-Z2 demo's block design, from its JSON.

    python3 -m revela.boards.pynq_z2.blockdesign --out <build dir>

The design is revela.boards.pynq_z2.pynq_demo. What it cannot know before
the build, the build supplies -- each from the file that owns it, never
restated:

  out_khz         the output raster's pixel clock, from np2hw's mode table
  sample_bits     the ISP's input width   \\
  isp_width       the width it was built   } from the ISP's published
  isp_out_bits    its traced output word  /  contract (<isp>_build.json)

and the board says what is possible: whether it can host the capture path,
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
from revela.boards import vivado
from revela.designs.composite import block_name, load

DESIGN = "revela.boards.pynq_z2.pynq_demo"


def facts(out: Path) -> dict:
    """The build's own facts, from their owners."""
    from np2hw.video_out import mode_timing
    c = choices()
    timing = mode_timing(c["mode"])
    contract_path = out / "hdl" / f"{block_name(c['pipeline'])}_build.json"
    if not contract_path.exists():
        raise SystemExit(f"blockdesign: {contract_path.name} is missing -- "
                         "generate the ISP first: its boundary is traced, "
                         "not declared")
    contract = json.loads(contract_path.read_text())
    boundary = contract["boundary"]
    isp_in = next(iter(boundary["inputs"].values()))
    isp_out = next(iter(boundary["outputs"].values()))
    if int(isp_in.get("channels", 1)) != 1:
        raise SystemExit("blockdesign: the ISP's input is not one sample "
                         "wide, and everything carrying it there is")
    return {"out_khz": int(round(float(timing["pixel_mhz"]) * 1000)),
            "sample_bits": int(isp_in["data_bits"]),
            "isp_width": int(contract["geometry"]["width"]),
            "isp_out_bits": int(isp_out["data_bits"])}


def check_board(c: dict, f: dict) -> None:
    """A choice met against a board fact."""
    from np2hw.video_out import mode_timing
    b = board()
    if c["capture"] and not b.get("capture", False):
        raise SystemExit("blockdesign: this board cannot host the capture "
                         f"path ({b.get('capture_note', 'see board.json')})")
    tx = default("hdmi_out", "max_pixel_khz") / 1000
    if f["out_khz"] / 1000 > tx:
        raise SystemExit(f"blockdesign: mode {c['mode']} needs "
                         f"{f['out_khz'] / 1000:g} MHz but hdmi_out is built "
                         f"for {tx:g}")
    rx = default("hdmi_in", "max_link_khz") / 1000
    link = float(mode_timing(c["link_mode"])["pixel_mhz"])
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
    f = facts(out)
    check_board(choices(), f)
    # The layout's checksum is built into the fabric, where software reads
    # it back; the layout does not depend on it, so a first load finds it.
    from revela.boards.zynq7.gpio import digest
    layout_id = digest(gpio_layout(load(DESIGN, build=f)))
    design = load(DESIGN, build={**f, "layout_id": layout_id})
    layout = gpio_layout(design)
    if digest(layout) != layout_id:
        raise SystemExit("blockdesign: the GPIO layout moved when its own "
                         "checksum was built in")
    paths = {}
    for line in (out / "generated" / "paths.tcl").read_text().splitlines():
        if line.startswith("set "):
            _, name, value = line.split(None, 2)
            paths[name] = value.strip("{}")
    b = board()
    tcl = vivado.emit(design, out=out, part=b["part"],
                      board_part=b["board_part"],
                      board_files=Path(paths["p_board_files"]),
                      ip_repos=[Path(paths["p_vivado_library"]),
                                out / "generated" / "busdef"])
    target = out / "generated" / "bd.tcl"
    target.write_text(tcl)
    (out / "generated" / "gpio_layout.json").write_text(
        json.dumps(layout, indent=1) + "\n")
    from np2hw.ipxact import write_definitions
    write_definitions(out / "generated" / "busdef")
    print(f"generated {target} -- {len(design.instances)} cells, "
          f"{f['sample_bits']}-bit into the ISP, {f['isp_out_bits']}-bit "
          f"out, {f['out_khz'] / 1000:g} MHz raster"
          + (", genlocked" if design.parameters["genlock"] else "")
          + (", capture" if design.parameters["capture"] else "")
          + f"; GPIO layout 0x{layout_id:08X}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
