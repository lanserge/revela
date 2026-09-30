# Copyright 2026 Serge Rabyking
# SPDX-License-Identifier: Apache-2.0 WITH SHL-2.1
"""The PYNQ-Z2 board builds as far as Vivado, with every check passing.

Everything before Vivado is generators and checks: the receiver, the ISP,
the raster, the block design's parameters and datapath, and the width of
every wire drawn between our own blocks. None of it needs Vivado, so all
of it is run here -- a board whose generators or checks fail is found in
seconds, not twenty minutes into a synthesis run.
"""
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from revela.boards import checkbd
from revela.boards import pynq_z2

BUILD = pynq_z2.HERE / "build.sh"


def test_block_design_script_passes_its_static_check():
    assert checkbd.check(pynq_z2.HERE / "bd.tcl") == 0


def test_scripts_read_only_sources_that_exist_beside_them():
    """Every file the Vivado scripts take from beside themselves exists
    there. What is generated lives in the build directory; a script
    reaching for it beside the sources finds nothing, and Vivado says so
    only after the block design has been half built."""
    import re
    for script in ("bd.tcl", "impl_a.tcl", "impl_b.tcl"):
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


def test_board_generates_and_checks_without_vivado(tmp_path):
    pytest.importorskip("bayerlink_fpga")
    out = tmp_path / "out"
    result = build(out, tmp_path)
    assert result.returncode == 0, result.stdout + result.stderr
    for name in ("params.tcl", "netlist.tcl", "receiver.tcl", "paths.tcl"):
        assert (out / "generated" / name).is_file(), name
    for name in ("bayerlink_rx.v", "ddc_slave.v", "revela_isp_core.v",
                 "scanout.v", "isp_tee.v"):
        assert (out / "hdl" / name).is_file(), name
    # Nothing is written beside the sources: they ship in the package.
    assert not (pynq_z2.HERE / "generated").exists()


def test_a_mis_sized_wire_stops_the_build(tmp_path):
    """Mutation check: a 5-bit pin wired to a 1-bit one is refused before
    Vivado, where the block design would connect the low bit and warn."""
    pytest.importorskip("bayerlink_fpga")
    board = tmp_path / "board"
    shutil.copytree(pynq_z2.HERE, board,
                    ignore=shutil.ignore_patterns("__pycache__"))
    bd = board / "bd.tcl"
    wire = "[get_bd_pins blrx/out_valid] [get_bd_pins probe/rx_valid]"
    text = bd.read_text()
    assert text.count(wire) == 1
    bd.write_text(text.replace(
        wire, "[get_bd_pins blrx/hdr_bits] [get_bd_pins probe/rx_valid]"))
    global BUILD
    saved, BUILD = BUILD, board / "build.sh"
    try:
        result = build(tmp_path / "out", tmp_path)
    finally:
        BUILD = saved
    assert result.returncode != 0
    assert "WIDTH MISMATCH" in result.stdout + result.stderr
    assert "blrx/hdr_bits" in result.stdout + result.stderr
