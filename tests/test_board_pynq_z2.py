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
import subprocess
import sys
from pathlib import Path

import pytest

from np2hw.toolchain.vivado import bdtrace
from revela.boards import pynq_z2

BUILD = pynq_z2.HERE / "build.sh"


def test_scripts_read_only_sources_that_exist_beside_them():
    """Every file the Vivado run scripts take from beside themselves exists
    there. What is generated lives in the build directory; a script
    reaching for it beside the sources finds nothing, and Vivado says so
    only after synthesis has begun."""
    import re
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


def test_the_demo_generates_and_checks_without_vivado(built):
    for name in ("paths.tcl", "bd.tcl"):
        assert (built / "generated" / name).is_file(), name
    for name in ("bayerlink_formats.v", "ddc_slave.v", "revela_isp_core.v",
                 "scanout.v", "out_cdc.v"):
        assert (built / "hdl" / name).is_file(), name
    # Fixed modules are read from the package that ships them, never
    # copied, and each is a source once however many blocks instantiate it
    bd = (built / "generated" / "bd.tcl").read_text()
    for package, module in (("np2hw", "np2hw_skid"), ("np2hw", "np2hw_tee"),
                            ("np2hw", "np2hw_cdc_fifo"),
                            ("bayerlink_fpga", "bayerlink_in")):
        assert bd.count(f"/{module}.v}}") == 1, module
        assert f"/{package}/hdl/{module}.v}}" in bd, module
        assert not (built / "hdl" / f"{module}.v").exists()
    # A fixed module is sized on its cell, from the values its leaf works
    # out: the receiver's lane order is the front end's, not the default.
    rx = next(line for line in bd.splitlines()
              if line.startswith("set_property") and "[get_bd_cells blrx]" in line)
    assert "CONFIG.LANE0 1 CONFIG.LANE1 2 CONFIG.LANE2 0" in rx, rx
    # Nothing is written beside the sources: they ship in the package.
    assert not (pynq_z2.HERE / "generated").exists()


def test_the_gpio_layout_is_the_fabrics(built):
    """The layout the bench reads comes from the same declaration as the
    fabric: the control word's defaults are the GPIO's power-on value,
    and each status word's fields fit the width the GPIO was built at."""
    layout = json.loads((built / "generated" / "gpio_layout.json").read_text())
    s = bdtrace.structure(bdtrace.trace(built / "generated" / "bd.tcl",
                                        [str(built)]))
    ctrl = layout["ctrl_gpio"]["channels"][0]
    # The power-on word is the whole declaration's: the consumer bit is
    # set even in a build without the capture path, as it always was.
    assert int(s["cells"]["ctrl_gpio"]["config"]["CONFIG.C_DOUT_DEFAULT"],
               16) == ctrl["default"] == 0x12
    assert "consumer" not in ctrl["fields"]     # capture is off here
    width = int(s["cells"]["status_gpio"]["config"]["CONFIG.C_GPIO_WIDTH"])
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
    s = bdtrace.structure(bdtrace.trace(built / "generated" / "bd.tcl",
                                        [str(built)]))
    held = int(s["cells"]["layout_const"]["config"]["CONFIG.CONST_VAL"])
    stated = layout["ctrl_gpio"]["channels"][1]["fields"]["layout_id"]
    assert held == stated["value"] == digest(layout) != 0
    assert stated["constant"] and stated["lsb"] == 0 and stated["bits"] == 32
    assert s["cells"]["ctrl_gpio"]["config"]["CONFIG.C_ALL_INPUTS_2"] == "1"
    assert ["ctrl_gpio/gpio2_io_i", "layout_const/dout"] in s["nets"]


def test_the_block_design_is_the_design(built):
    """The generated script, read back: the jacks' cells are there under
    their own names, the capture path only where the design asks for it,
    and every pin the constraints name is a port."""
    s = bdtrace.structure(bdtrace.trace(built / "generated" / "bd.tcl",
                                        [str(built)]))
    for cell in ("ps7", "dvi_rx", "hdmi_tx", "blrx", "isp", "scanout",
                 "ddc_phy", "rst_bus"):
        assert cell in s["cells"], cell
    assert "dma" not in s["cells"]            # capture is off by default
    assert set(s["ports"]) == {"TMDS", "hdmi_hpd", "ddc_scl_io",
                               "ddc_sda_io", "hdmi_tx"}
    assert s["cells"]["dvi_rx"]["config"]["CONFIG.kClkRange"] == "1"
    # the VDMA derives its stream width and its clocking from what it is
    # connected to, and refuses both as settings: neither is restated
    vdma = s["cells"]["vdma"]["config"]
    assert "CONFIG.c_s_axis_s2mm_tdata_width" not in vdma
    assert "CONFIG.c_prmry_is_aclk_async" not in vdma


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
    # no net is driven twice, and every net's stated pins agree in width
    for net in netlist["nets"]:
        assert len(net["drivers"]) <= 1
        assert len(set(net["widths"].values())) <= 1
