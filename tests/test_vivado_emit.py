# Copyright 2026 Serge Rabyking
# SPDX-License-Identifier: Apache-2.0 WITH SHL-2.1
"""A design's block design, rendered for Vivado from its JSON.

The rendering is read back with the same stubs revela.boards.bdtrace uses,
so what is checked is the design the script builds -- its cells, their
settings, its nets -- not the text of the script.
"""
import itertools
import json

import pytest

from revela.boards import bdtrace, vivado
from revela.designs.composite import load

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


def test_cells_settings_pins_and_nets(library, tmp_path):
    lib = library({"top": {
        "parameters": {"n": {"default": 2}},
        "pins": [{"name": "led", "dir": "O"}],
        "nodes": {"one": {"block": f"{IP}:xlconstant",
                          "config": {"CONST_VAL": "1"}},
                  "cat": {"block": f"{IP}:xlconcat", "build": {"NUM_PORTS": "n"}}},
        "connections": [["one.dout", "cat.In0"], ["one.dout", "cat.In1"],
                        ["cat.dout", "led"]]}})
    tcl, s = render(load(f"{lib}.lib:top"), tmp_path)
    assert s["cells"]["one"] == {"type": "ip", "of": "xilinx.com:ip:xlconstant",
                                 "config": {"CONFIG.CONST_VAL": "1"}}
    assert s["cells"]["cat"]["config"] == {"CONFIG.NUM_PORTS": "2"}
    assert s["ports"] == {"led": "O"}
    assert sorted(s["nets"]) == [["cat/In0", "cat/In1", "one/dout"],
                                 ["cat/dout", "led"]]
    # one net, one command, however many connections made it
    assert tcl.count("connect_bd_net [get_bd_pins one/dout]") == 1


def test_interfaces_and_external_interface_pins(library, tmp_path):
    lib = library({"top": {
        "pins": [{"name": "TMDS", "interface": True}],
        "nodes": {"rx": {"block": f"{IP}:dvi2rgb"},
                  "cdc": {"block": f"{IP}:axis_clock_converter"},
                  "cdc2": {"block": f"{IP}:axis_clock_converter"}},
        "connections": [["rx.TMDS", "TMDS"], ["cdc.M_AXIS", "cdc2.S_AXIS"]]}})
    tcl, s = render(load(f"{lib}.lib:top"), tmp_path)
    assert s["intf_nets"] == [["cdc/M_AXIS", "cdc2/S_AXIS"]]
    assert s["ports"] == {"TMDS": "external rx/TMDS"}


def test_a_procedure_runs_in_place_and_a_late_one_after_the_nets(library, tmp_path):
    (tmp_path / "p.tcl").write_text("")
    lib = library({"top": {
        "nodes": {"ps7": {"block": "revela.boards.zynq7.ps7",
                          "config": {"PCW_USE_S_AXI_HP0": "1"}},
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
    assert s["cells"]["ps7"]["config"] == {"CONFIG.PCW_USE_S_AXI_HP0": "1"}
    assert s["automation"][0][:2] == ["-rule", "xilinx.com:bd_rule:processing_system7"]
    assert s["addresses"] == [[]]


def test_two_leaves_that_would_be_one_cell_are_refused(library, tmp_path):
    lib = library(lambda lib: {
        "jack": {"nodes": {"k": {"block": f"{IP}:xlconstant"}}, "connections": []},
        "top": {"nodes": {"a": {"block": f"{lib}.lib:jack"},
                          "b": {"block": f"{lib}.lib:jack"}}, "connections": []}})
    with pytest.raises(vivado.EmitError, match="both be the Vivado cell 'k'"):
        render(load(f"{lib}.lib:top"), tmp_path)


STREAM = ["sof", "eol", "last"]
# a source and a sink of one stream, as their library entries state them
SOURCE = {"vivado": {"module": "src"}, "sources": ["src.v"],
          "inputs": [{"name": "clk"}],
          "outputs": [{"name": "out", "width": 8, "stream": STREAM}]}
SINK = {"vivado": {"module": "snk"}, "sources": ["snk.v"],
        "inputs": [{"name": "clk"},
                   {"name": "in", "width": 8, "stream": STREAM}]}


def _streams(tap):
    def blocks(lib):
        top = {"nodes": {"s": {"block": f"{lib}.lib:src"},
                         "d": {"block": f"{lib}.lib:snk"}},
               "connections": [["s.out", "d.in"]]}
        if tap:
            top["nodes"]["t"] = {"block": f"{IP}:xlconcat"}
            top["connections"].append(["s.out_valid", "t.In0"])
        return {"src": SOURCE, "snk": SINK, "top": top}
    return blocks


def test_a_stream_is_one_interface_net_unless_a_pin_of_it_is_tapped(
        library, tmp_path):
    """A stream between two declared interfaces is one interface net. But
    Vivado lets a net on one pin of an interface override the interface's
    connection of it, so a stream with a tapped pin is joined signal by
    signal -- the tap on the same net as the sink, not instead of it."""
    _, s = render(load(f"{library(_streams(False))}.lib:top"), tmp_path)
    assert s["intf_nets"] == [["d/in_stream", "s/out_stream"]]
    assert not any("s/out_valid" in n for n in s["nets"])
    _, s = render(load(f"{library(_streams(True))}.lib:top"), tmp_path)
    assert s["intf_nets"] == []
    assert ["d/in_valid", "s/out_valid", "t/In0"] in s["nets"]
    assert ["d/in_data", "s/out_data"] in s["nets"]
