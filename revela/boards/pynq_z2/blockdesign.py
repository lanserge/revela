# Copyright 2026 Serge Rabyking
# SPDX-License-Identifier: Apache-2.0 WITH SHL-2.1
"""Write the PYNQ-Z2 demo's block design, from its JSON.

    python3 -m revela.boards.pynq_z2.blockdesign --out <build dir>

The design is revela.boards.pynq_z2.pynq_demo. Loading it builds the ISP
inside it, and the ISP's widths reach everything around it as the design's
own parameters, assigned from the ISP's facts; the raster's pixel clock is
one of its parameters too, read from bayerlink's rasters by an expression.
The board says what is possible: whether it can host the capture path,
and how fast each HDMI jack runs. A choice the board cannot honour is
refused here, by name, rather than by a cascade of Tcl errors in Vivado.

Writes the design as Verilog into <build dir>/hdl and <build dir>/generated/
rtl.tcl, which build.sh hands Vivado to make its own blocks and the build,
and
gpio_layout.json: the GPIO fields by name, from the same declarations,
for the bench scripts -- which never write a bit position down. Its
checksum is built into the fabric as a constant GPIO word, so a script
handed a layout from another build refuses it instead of misreading.
And address_map.json: where each register slave sits, by its cell's name,
from the windows the design's bus placed -- the host reads addresses
there, because the hardware handoff cannot see through the bus. And
cable_map.json, the same for the bus the cable's source reaches: the
ISP's window, and the raster's registers field by field with their ID.
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
# The two masters with register buses of their own: the processor's, and
# the cable's -- the source that owns the sensor, over the DDC.
HOST, CABLE = "gp0.m_axi", "fabric.ddc.M_AXI"


def check_board(c: dict) -> None:
    """The design's choices met against the board's facts."""
    b = board()
    if c["capture"] and not b.get("capture", False):
        raise SystemExit("blockdesign: this board cannot host the capture "
                         f"path ({b.get('capture_note', 'see board.json')})")
    tx = default("hdmi_out", "max_pixel_khz") / 1000
    if c["out_khz"] / 1000 > tx:
        raise SystemExit(f"blockdesign: mode {c['mode']} needs "
                         f"{c['out_khz'] / 1000:g} MHz but hdmi_out is built "
                         f"for {tx:g}")

def check_link(design) -> None:
    """The link the design advertises, met against the front end that
    receives it: the jack is constrained at the link's own rate and the
    front end set for it, so the one limit left is the front end's
    slowest (its fact min_link_khz)."""
    rx = design.instances["hdmi_in.dvi_rx"].leaf
    low = rx.get("filled_by", {}).get("chosen", {}).get(
        "facts", {}).get("min_link_khz")
    link = design.pins["TMDS"]["clock"]["khz"]
    if low is not None and link < low:
        raise SystemExit(f"blockdesign: the link runs at {link / 1000:g} MHz, "
                         f"below the {low / 1000:g} MHz the HDMI front end "
                         "can be set for")


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
        # a GPIO this build leaves out (its node's `when` false) has no
        # cell, and no window in the address map: software is not told of
        # one it cannot reach
        if config["gpio"] not in by_cell:
            continue
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
                        by_cell[f["cell"]].build["VALUE"]
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
    check_link(design)
    layout = gpio_layout(design)
    if digest(layout) != layout_id:
        raise SystemExit("blockdesign: the GPIO layout moved when its own "
                         "checksum was built in")
    # Anything a host can reach must answer: every bus clock and reset the
    # board's, never the cable's -- refused here, before a build, not on
    # the bench after a hang.
    from np2hw.design.ancestry import audit
    problems = audit(design)
    if problems:
        raise SystemExit("blockdesign: a bus that a cable can stop:\n  "
                         + "\n  ".join(problems))
    paths = {}
    for line in (out / "generated" / "paths.tcl").read_text().splitlines():
        if line.startswith("set "):
            _, name, value = line.split(None, 2)
            paths[name] = value.strip("{}")
    # The generated leaves' Verilog, made by their generators from this
    # design's settings, beside the rest of the build's.
    write_generated(design, out / "hdl")
    b = board()
    # The design as Verilog, one module per composite, and the Tcl that has
    # Vivado make only its own blocks and synthesise it
    # (np2hw.toolchain.vivado.rtl).
    from np2hw.toolchain.vivado.rtl import emit_rtl
    tcl, files, facts = emit_rtl(
        design, out=out, part=b["part"], board_part=b["board_part"],
        board_files=Path(paths["p_board_files"]),
        ip_repos=[Path(paths["p_vivado_library"]),
                  out / "generated" / "busdef"],
        # the project, by the name the board's flow scripts read
        name="rx")
    for file, text in files.items():
        (out / "hdl" / file).write_text(text)
    target = out / "generated" / "rtl.tcl"
    target.write_text(tcl)
    # what the implementation steps read: the top
    (out / "generated" / "flow.tcl").write_text(
        "# GENERATED by blockdesign.py -- do not edit.\n"
        f"set p_top {facts['top']}\n")
    # what each described vendor block should be once the tool has built
    # it, for np2hw.toolchain.vivado.vendor_check after rtl.tcl runs
    from np2hw.toolchain.vivado.vendor_check import expected
    (out / "generated" / "vendor_expected.json").write_text(json.dumps(
        expected(design, vivado.cell_names(design)), indent=1) + "\n")
    # the build's product, for any tool that reads it
    (out / "generated" / "netlist.json").write_text(
        json.dumps(design.netlist, indent=1, default=str) + "\n")
    (out / "generated" / "gpio_layout.json").write_text(
        json.dumps(layout, indent=1) + "\n")
    # where each register slave sits, by its cell's name, from the windows
    # the bus published: the host's map, since Vivado cannot see through
    # our decoder to put the slaves in the hardware handoff
    from np2hw.bus import address_map
    cells = vivado.cell_names(design)
    (out / "generated" / "address_map.json").write_text(json.dumps(
        {cells[path]: {"base": w["base"], "size": w["size"]}
         for path, w in address_map(design, HOST).items()}, indent=1) + "\n")
    # and the far end's: what the source owning the sensor reaches over
    # the cable, each slave with its fields' offsets and its ID word
    # where it publishes them (the ISP's travel in its own pack)
    def far(path, window):
        facts = design.instances[path].leaf.get("facts", {})
        entry = {"base": window["base"], "size": window["size"]}
        if "offsets" in facts:
            entry.update(registers=facts["offsets"], id=facts["id"])
        if path.rpartition(".")[2] == "isp":
            # the clock the ISP takes a sample a cycle on: how fast a
            # source may hand it samples, traced to the fabric clock
            # that drives it
            entry["clock_khz"] = isp_khz()
        return entry
    def isp_khz():
        from revela.boards.zynq7 import fabric_clocks
        net = next(n["pins"] for n in design.netlist["nets"]
                   if "fabric.isp.clk" in n["pins"])
        fclk = [p for p in net if p.startswith("ps7.FCLK_CLK")]
        if len(fclk) != 1:
            raise ValueError(f"the ISP's clock is driven by {fclk}, not one "
                             "fabric clock")
        i = fclk[0][len("ps7.FCLK_CLK"):]
        return round(fabric_clocks(design)[f"fclk{i}_mhz"] * 1000)
    def display_clocks():
        # each row's clock, from the clock manager configured to make it:
        # a fine step is a 56th of a VCO period, so a pixel is 56 times
        # its output's divider in steps; and the serializer MMCM's recipe
        # for that clock
        from revela.boards.common.clocking import (STEPS_PER_VCO_PERIOD,
                                                   tmds_recipe)
        made = {}
        for i in design.instances.values():
            c = i.config
            if "MMCM_CLKOUT0_DIVIDE_F" not in c:
                continue
            outs = [("CLKOUT1_REQUESTED_OUT_FREQ", "MMCM_CLKOUT0_DIVIDE_F")]
            if "MMCM_CLKOUT1_DIVIDE" in c:
                outs.append(("CLKOUT2_REQUESTED_OUT_FREQ",
                             "MMCM_CLKOUT1_DIVIDE"))
            for freq, divide in outs:
                made[round(float(c[freq]) * 1000)] = round(
                    STEPS_PER_VCO_PERIOD * float(c[divide]))
        return [{"khz": int(k), "ps_per_px": made[int(k)],
                 "pll": tmds_recipe(int(k))}
                for k in design.parameters["tv_khz_rows"]]
    # the display's counters, which bound the rasters it can scan
    raster = next(i for p, i in design.instances.items()
                  if p.endswith(".scanout"))
    counters = {k: int(raster.build[k]) for k in ("XB", "YB")}
    (out / "generated" / "cable_map.json").write_text(json.dumps(
        {path.rpartition(".")[2]: dict(far(path, w), **(
            {"counter_bits": [counters["XB"], counters["YB"]],
             # the display's clocks, row by row of its clock select, with
             # the fine steps each row's pixel is: a raster at any other
             # clock is refused
             "clocks": display_clocks()}
            if "offsets" in design.instances[path].leaf.get("facts", {})
            else {}))
         for path, w in address_map(design, CABLE).items()},
        indent=1) + "\n")
    # the fabric clocks the processor drives, which a host sets before it
    # programs the fabric: the design states them, and nothing needs the
    # hardware handoff to read them back
    from revela.boards.zynq7 import fabric_clocks
    (out / "generated" / "clocks.json").write_text(
        json.dumps(fabric_clocks(design), indent=1) + "\n")
    from np2hw.ipxact import write_definitions
    write_definitions(out / "generated" / "busdef")
    p = design.parameters
    print(f"generated {target} -- {len(design.instances)} cells, "
          f"{p['sample_bits']}-bit into the ISP, {p['isp_out_bits']}-bit "
          f"out, {p['out_khz'] / 1000:g} MHz raster, genlocked"
          + (", capture" if design.parameters["capture"] else "")
          + f"; GPIO layout 0x{layout_id:08X}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
