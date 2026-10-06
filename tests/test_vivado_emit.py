# Copyright 2026 Serge Rabyking
# SPDX-License-Identifier: Apache-2.0 WITH SHL-2.1
"""revela's board blocks, rendered for Vivado.

The emitter is np2hw's and is tested there; this holds revela's
descriptions -- the HDMI transmitter, the stream DMA, the processing system
and its bus -- to the block design they make. The rendering is read back
with np2hw.toolchain.vivado.bdtrace's stubs, so what is checked is the
design the script builds, not the text of the script.
"""
import itertools
import json

import pytest

from np2hw.toolchain import vivado
from np2hw.toolchain.vivado import bdtrace
from np2hw.design.composite import load

IP = "revela.boards.zynq7.ip"
_count = itertools.count()


@pytest.fixture
def library(tmp_path, monkeypatch):
    monkeypatch.syspath_prepend(str(tmp_path))

    def make(blocks) -> str:
        """``blocks`` is a dict, or a function of the package name
        returning one, so a block can reference its own library."""
        name = f"emit_lib_{next(_count)}"
        package = tmp_path / name
        package.mkdir()
        (package / "__init__.py").write_text("")
        if callable(blocks):
            blocks = blocks(name)
        (package / "lib.json").write_text(json.dumps({"blocks": blocks}))
        return name
    return make


def render(design, tmp_path):
    out = tmp_path / "out"
    (out / "hdl").mkdir(parents=True, exist_ok=True)
    tcl = vivado.emit(design, out=out, part="xc7z020clg400-1",
                      board_part="tul.com.tw:pynq-z2:part0:1.0",
                      board_files=tmp_path, ip_repos=[tmp_path])
    script = tmp_path / "bd.tcl"
    script.write_text(tcl)
    return tcl, bdtrace.structure(bdtrace.trace(script, [str(out)]))


def test_the_transmitter_and_the_dma_render_by_their_implementations(
        library, tmp_path):
    lib = library({"top": {
        "pins": [{"name": "TMDS", "interface": True}],
        "nodes": {"rx": {"block": "revela.boards.common.hdmi_tx",
                         "build": {"pixel_khz": 74250}},
                  "pack": {"block": "revela.boards.common.modules:isp_axis",
                           "build": {"DATA_BITS": 24}},
                  "dma": {"block": "revela.boards.common.stream_dma"}},
        "connections": [["rx.TMDS", "TMDS"], ["pack.m_axis", "dma.stream"]]}})
    tcl, s = render(load(f"{lib}.lib:top"), tmp_path)
    # one interface net, by the implementation's name for the block's port
    assert s["intf_nets"] == [["dma/S_AXIS_S2MM", "pack/m_axis"]]
    assert s["ports"] == {"TMDS": "external rx/TMDS"}


def test_the_processing_system_is_built_before_the_nets_and_the_bus_after(
        library, tmp_path):
    (tmp_path / "p.tcl").write_text("")
    lib = library({"top": {
        "nodes": {"ps7": {"block": "revela.boards.zynq7.ps7",
                          "build": {"hp0": 1},
                          "config": {"PCW_FPGA0_PERIPHERAL_FREQMHZ": "100"}},
                  "k": {"block": f"{IP}:xlconstant"},
                  "bus": {"block": "revela.boards.zynq7.bus:axi_bus",
                          "config": {"bus_reset": "r/o", "link_reset": "l/o",
                                     "link_reset_keeps": [],
                                     "control_interconnect": "ic",
                                     "island_clock": "/ps7/FCLK_CLK0",
                                     "steps": [{"addresses": []}]}}},
        "connections": [["ps7.FCLK_CLK0", "k.x"]]}})
    tcl, s = render(load(f"{lib}.lib:top"), tmp_path)
    lines = tcl.splitlines()
    ps = next(i for i, l in enumerate(lines) if l.startswith("zynq7_ps7 ps7"))
    net = next(i for i, l in enumerate(lines) if l.startswith("connect_bd_net"))
    bus = next(i for i, l in enumerate(lines) if l.startswith("zynq7_axi_bus bus"))
    assert ps < net < bus
    # the block's settings, from its parameters, and the node's own
    assert s["cells"]["ps7"]["config"] == {
        "CONFIG.PCW_USE_S_AXI_HP0": "1", "CONFIG.PCW_USE_S_AXI_HP1": "0",
        "CONFIG.PCW_EN_CLK1_PORT": "0", "CONFIG.PCW_USE_FABRIC_INTERRUPT": "0",
        "CONFIG.PCW_IRQ_F2P_INTR": "0",
        "CONFIG.PCW_FPGA0_PERIPHERAL_FREQMHZ": "100"}
    assert s["automation"][0][:2] == ["-rule", "xilinx.com:bd_rule:processing_system7"]
    assert s["addresses"] == [[]]
