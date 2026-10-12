# Copyright 2026 Serge Rabyking
# SPDX-License-Identifier: Apache-2.0 WITH SHL-2.1
"""The PYNQ-Z2 demo builds as far as Vivado, with every check passing.

Everything before Vivado is generators and checks: the receiver, the ISP,
the raster, and the block design -- generated from the design's JSON, with
the width of every wire between our own modules checked. None of it needs
Vivado, so all of it is run here: a design whose generators or checks fail
is found in seconds, not twenty minutes into a synthesis run.
"""
import json
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

from revela.boards import pynq_z2

BUILD = pynq_z2.HERE / "build.sh"


def test_scripts_read_only_sources_that_exist_beside_them():
    """Every file the Vivado run scripts take from beside themselves exists
    there. What is generated lives in the build directory; a script
    reaching for it beside the sources finds nothing, and Vivado says so
    only after synthesis has begun."""
    for script in ("impl_a.tcl", "impl_b.tcl"):
        text = (pynq_z2.HERE / script).read_text()
        for parts in re.findall(r"\[file join \$here ([^\]]+)\]", text):
            path = pynq_z2.HERE.joinpath(*parts.split())
            assert path.is_file(), f"{script}: {path} does not exist"


def build(out: Path, tmp_path: Path) -> subprocess.CompletedProcess:
    # The vendor trees are only looked for before Vivado; empty stand-ins
    # say so without cloning them.
    for tree in ("board-files", "vivado-library"):
        (tmp_path / tree).mkdir(exist_ok=True)
    env = dict(os.environ, NO_VIVADO="1", OUT=str(out),
               BOARD_FILES=str(tmp_path / "board-files"),
               VIVADO_LIBRARY=str(tmp_path / "vivado-library"),
               PATH=f"{Path(sys.executable).parent}{os.pathsep}"
                    f"{os.environ['PATH']}")
    return subprocess.run(["sh", str(BUILD)], cwd=tmp_path, env=env,
                          capture_output=True, text=True)


@pytest.fixture(scope="module")
def built(tmp_path_factory):
    pytest.importorskip("bayerlink_fpga")
    tmp = tmp_path_factory.mktemp("pynq_z2")
    out = tmp / "out"
    result = build(out, tmp)
    assert result.returncode == 0, result.stdout + result.stderr
    return out


def ip_config(built, module):
    """The settings ``rtl.tcl`` gives the vendor IP built as ``module``."""
    tcl = (built / "generated" / "rtl.tcl").read_text()
    out = {}
    for line in tcl.splitlines():
        m = re.fullmatch(r"set_property -dict \[list (.*)\] "
                         rf"\[get_ips {module}\]", line)
        if m:
            out.update(re.findall(r"CONFIG\.(\w+) \{?([^ }]*)\}?",
                                  m.group(1)))
    return out


def instance(built, name):
    """``(module, parameters, connections)`` of the cell ``name`` in the
    Verilog the build wrote."""
    for v in (built / "hdl").glob("*.v"):
        m = re.search(rf"^[ ]+(\w+)[ ]*(?:#\(([^\n]*)\))?[ ]+{name} \((.*?)\);",
                      v.read_text(), re.M | re.S)
        if m:
            params = dict(re.findall(r"\.(\w+)\(([^()]*)\)", m.group(2) or ""))
            conns = dict(re.findall(r"\.(\w+)\(([^()]*)\)", m.group(3)))
            return m.group(1), params, conns
    raise AssertionError(f"no cell {name} in the written Verilog")


def test_the_demo_generates_and_checks_without_vivado(built):
    for name in ("paths.tcl", "rtl.tcl", "flow.tcl"):
        assert (built / "generated" / name).is_file(), name
    for name in ("bayerlink_formats.v", "ddc_slave.v", "revela_isp_core.v",
                 "scanout_regs.v", "pynq_demo.v"):
        assert (built / "hdl" / name).is_file(), name
    # Fixed modules are read from the package that ships them, never
    # copied, and each is a source once however many blocks instantiate it
    tcl = (built / "generated" / "rtl.tcl").read_text()
    for package, module in (("np2hw", "np2hw_skid"), ("np2hw", "np2hw_tee"),
                            ("np2hw", "np2hw_cdc_fifo"),
                            ("bayerlink_fpga", "bayerlink_in")):
        assert tcl.count(f"/{module}.v}}") == 1, module
        assert f"/{package}/hdl/{module}.v}}" in tcl, module
        assert not (built / "hdl" / f"{module}.v").exists()
    # A fixed module is sized on its cell, from the values its leaf works
    # out: the receiver's lane order is the front end's, not the default.
    module, params, _ = instance(built, "blrx")
    assert module == "bayerlink_in"
    assert (params["LANE0"], params["LANE1"], params["LANE2"]) == \
        ("1", "2", "0"), params
    # Nothing is written beside the sources: they ship in the package.
    assert not (pynq_z2.HERE / "generated").exists()


def test_the_gpio_layout_is_the_fabrics(built):
    """The layout the bench reads comes from the same declaration as the
    fabric: the control word's defaults are the GPIO's power-on value,
    and each status word's fields fit the width the GPIO was built at."""
    layout = json.loads((built / "generated" / "gpio_layout.json").read_text())
    ctrl = layout["ctrl_gpio"]["channels"][0]
    # The power-on word is the whole declaration's: the consumer bit is
    # set even in a build without the capture path, as it always was.
    assert int(ip_config(built, "ctrl_gpio")["C_DOUT_DEFAULT"], 16) == \
        ctrl["default"] == 0x12
    assert "consumer" not in ctrl["fields"]     # capture is off here
    width = int(ip_config(built, "status_gpio")["C_GPIO_WIDTH"])
    top = max(f["lsb"] + f["bits"] for f in
              layout["status_gpio"]["channels"][0]["fields"].values())
    assert top <= width
    assert layout["status_gpio"]["channels"][0]["fields"]["hdr_valid"] == {
        "lsb": 27, "bits": 1, "description": "A header has been accepted."}


def test_the_layout_checksum_is_built_into_the_fabric(built):
    """The constant the fabric holds is the checksum of the layout file
    written beside it, and the file states it -- so a script can tell a
    layout from another build."""
    from revela.boards.zynq7.gpio import digest
    layout = json.loads((built / "generated" / "gpio_layout.json").read_text())
    module, params, const = instance(built, "layout_const")
    assert module == "np2hw_const"
    held = int(params["VALUE"].split("'d")[-1])
    stated = layout["ctrl_gpio"]["channels"][1]["fields"]["layout_id"]
    assert held == stated["value"] == digest(layout) != 0
    assert stated["constant"] and stated["lsb"] == 0 and stated["bits"] == 32
    assert ip_config(built, "ctrl_gpio")["C_ALL_INPUTS_2"] == "1"
    _, _, gpio = instance(built, "ctrl_gpio")
    assert gpio["gpio2_io_i"] == const["q"]


def test_the_top_is_the_design(built):
    """The written Verilog, read back: the jacks' cells are there under
    their own names, the capture path only where the design asks for it,
    and every pin the constraints name is a port of the top."""
    for cell in ("dvi_rx", "hdmi_tx", "blrx", "isp", "scanout", "ddc_phy",
                 "rst_bus"):
        instance(built, cell)
    # the processor is one of the cells the block design holds
    tcl = (built / "generated" / "rtl.tcl").read_text()
    assert "create_bd_design pynq_demo_bd" in tcl
    assert "zynq7_ps7 ps7 " in tcl
    with pytest.raises(AssertionError):
        instance(built, "dma")                # capture is off by default
    top = (built / "hdl" / "pynq_demo.v").read_text()
    ports = set(re.findall(r"^\s+(?:input|output|inout)\s+wire\s+"
                           r"(?:\[\d+:0\]\s+)?(\w+)", top, re.M))
    named = set()
    for xdc in pynq_z2.HERE.glob("*.xdc"):
        named |= {re.sub(r"\[\d+\]$", "", n) for n in re.findall(
            r"get_ports \{?([\w\[\]]+)\}?\]", xdc.read_text())}
    assert named and named <= ports, named - ports
    # the jack is constrained at the link it advertises, and the front end
    # set for it: a 74.25 MHz link, range 2 (its VCO at 742.5 MHz)
    from revela.boards.common.clocking import pixel_khz
    from revela.boards.pynq_z2 import choices
    khz = pixel_khz(choices()["link_mode"])
    clocks = (built / "hdl" / "rx_pin_clocks.xdc").read_text()
    assert f"create_clock -period {1e6 / khz:.3f} -name TMDS_clk " \
        "[get_ports TMDS_clk_p]" in clocks
    assert khz == 74250 and ip_config(built, "dvi_rx")["kClkRange"] == "2"
    assert not any("create_clock" in line and "TMDS" in line
                   for xdc in pynq_z2.HERE.glob("*.xdc")
                   for line in xdc.read_text().splitlines()
                   if not line.lstrip().startswith("#"))
    # the VDMA's clocking follows its wiring: all three clocks are FCLK0
    # in this build, so it is built synchronous
    assert ip_config(built, "vdma")["c_prmry_is_aclk_async"] == "0"


def test_a_mis_wired_design_is_refused_before_vivado(built, tmp_path,
                                                     monkeypatch):
    """Mutation check: the demo with one 5-bit pin wired to a 1-bit one
    is refused as soon as it is loaded, naming the wire -- before any
    emitter, for any toolchain."""
    from np2hw.design.composite import CompositionError, load
    design = json.loads((pynq_z2.HERE / "pynq_demo.json").read_text())
    conns = design["blocks"]["pynq_demo"]["connections"]
    i = conns.index(["control.broom", "fabric.broom"])
    conns[i] = ["fabric.hdr_bits", "fabric.broom"]
    package = tmp_path / "mutant_demo"
    package.mkdir()
    (package / "__init__.py").write_text("")
    (package / "pynq_demo.json").write_text(json.dumps(design))
    monkeypatch.syspath_prepend(str(tmp_path))
    with pytest.raises(CompositionError, match=r"WIDTH MISMATCH.*hdr_bits"):
        load("mutant_demo.pynq_demo")


def test_the_capture_variant_crosses_with_np2hw():
    """The capture path is the board's: a switch after the receiver, then
    np2hw's stream crossing into the DMA's clock -- no vendor converter --
    and the stream dressed as AXI4-Stream only once it is there. Loaded,
    and so wired and checked, like the default variant."""
    from np2hw.design.composite import load
    design = load("revela.boards.pynq_z2.pynq_demo", build={"capture": 1})
    cells = {p.rpartition(".")[2]: i for p, i in design.instances.items()}
    assert cells["cdc"].leaf["vivado"] == {"module": "np2hw_stream_cdc"}
    assert "dma" in cells and "sw" in cells and "shim" in cells
    assert not any(i.leaf.get("vivado", {}).get("ip", "").endswith(
        "axis_clock_converter") for i in design.instances.values()
        if isinstance(i.leaf, dict))
    assert ("sw.a", "cdc.in") in design.edges
    assert ("cdc.out", "shim.in") in design.edges


def test_the_build_writes_its_netlist(built):
    """The netlist is the build's product: every leaf with its
    implementation and resolved ports, every bundle and net -- the one
    statement of the wiring, written beside the block design."""
    netlist = json.loads((built / "generated" / "netlist.json").read_text())
    assert set(netlist) >= {"design", "instances", "bundles", "nets", "pins"}
    rx = netlist["instances"]["fabric.blrx"]
    assert rx["implementation"]["module"] == "bayerlink_in"
    assert rx["ports"]["out_data"] == ["output", 12]
    tx = netlist["instances"]["hdmi_out.hdmi_tx"]
    assert tx["implementation"]["implementation"] == "rgb2dvi"
    # the receiver's stream to the board and back is one bundle connection
    assert {"from": "fabric.blrx.out", "to": "fabric.isp_cdc.in"} in \
        netlist["bundles"]
    # no net is driven twice, and every net's stated pins agree in width --
    # but an address, of which each end uses the bits it decodes
    for net in netlist["nets"]:
        assert len(net["drivers"]) <= 1
        assert len(set(net["widths"].values())) <= 1 or net.get("ranged")
    # a bundle is its members' nets: the raster reaches the transmitter
    # signal by signal, though the two name their bundles differently
    nets = [set(net["pins"]) for net in netlist["nets"]]
    for src, dst in (("vid_active_video", "rgb_active_video"),
                     ("vid_data", "rgb_data"), ("vid_hsync", "rgb_hsync"),
                     ("vid_vsync", "rgb_vsync")):
        assert {f"fabric.scanout.{src}", f"hdmi_out.hdmi_tx.{dst}"} in [
            n & {f"fabric.scanout.{src}", f"hdmi_out.hdmi_tx.{dst}"}
            for n in nets], (src, dst)


def test_every_bus_answers_whatever_the_cable_does():
    """Every clock and reset of every bus, in both variants, traced through
    the netlist to the processor or a constant: none to the cable."""
    from np2hw.design.ancestry import audit
    from np2hw.design.composite import load
    for build in ({}, {"capture": 1}):
        assert audit(load("revela.boards.pynq_z2.pynq_demo",
                          build=build)) == [], build



@pytest.mark.parametrize("mode, divide, steps", [("1080p30", "10.000", 560),
                                                  ("1080p60", "5.000", 280)])
def test_the_raster_genlocks_at_the_rate_its_mode_runs(mode, divide, steps):
    """One recipe, every mode it divides to: the raster's clock makes the
    mode's pixel rate exactly, and the scanout counts the fine steps that
    clock's pixel is -- both from the recipe, so they cannot disagree."""
    from np2hw.design.composite import load
    design = load("revela.boards.pynq_z2.pynq_demo",
                  build={"link_mode": repr(mode)})
    clock = design.instances["clk_raster"].config
    assert clock["MMCM_CLKOUT0_DIVIDE_F"] == divide
    assert clock["MMCM_CLKFBOUT_MULT_F"] == "37.125"
    regs = design.instances["fabric.scanout_regs"].leaf["files"][
        "scanout_regs.v"]
    assert re.search(rf"reg_ps_per_px\s*<=\s*{steps};", regs), \
        "the raster's steps, as its register resets"


def test_a_rate_the_recipe_cannot_make_is_refused():
    """480p's 27 MHz is not a whole division of the 742.5 MHz VCO: the
    build says so rather than letting the wizard land somewhere near."""
    from np2hw.design.composite import load
    with pytest.raises(Exception, match="does not divide"):
        load("revela.boards.pynq_z2.pynq_demo",
             build={"mode": "'480p60'"}, generate=False)
