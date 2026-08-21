# Copyright 2026 Serge Rabyking
# SPDX-License-Identifier: Apache-2.0 WITH SHL-2.1
"""FuseSoC design packs: revela's policy in, one manifest out.

The packaging protocol itself -- generator input parsing, the .core
writer -- is np2hw's and is proven by np2hw's own example suite. What is
under test here is revela's half only: a pipeline JSON becomes a pack whose
Verilog is byte-identical to a direct build, whose manifest names exactly
what was written, and whose generator entry point speaks the protocol.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from revela import designs, fusesoc

ROOT = Path(__file__).resolve().parent.parent
MONO = ROOT / "pipelines" / "mono" / "imx219" / "basic" / "pipeline.json"


def test_emit_writes_a_pack_identical_to_a_direct_build(tmp_path):
    """The pack is a delivery format, not a second generator.

    Byte-identical Verilog is the claim that makes the pack safe: whoever
    consumes the core through FuseSoC gets exactly what a direct
    ``Pipeline.generate()`` produces, so there is no packaging-time variant
    to verify separately.
    """
    # verify=False THROUGHOUT this file's packaging tests: the twin proof
    # is emit's own default, exercised by the gate tests below on a small
    # design -- re-running Verilator over a full imx219 frame per
    # packaging assertion tests nothing further about packaging.
    written = fusesoc.emit(MONO, tmp_path, verify=False)
    direct = designs.load(MONO).generate(control=True)
    assert written["verilog"].read_text() == direct.verilog
    assert written["toplevel"] == direct.top

    manifest = written["core"].read_text()
    assert manifest.startswith("CAPI=2:")
    for artifact in ("verilog", "regmap", "systemrdl"):
        name = written[artifact].name
        assert written[artifact].exists()
        assert f"- {name}" in manifest, f"{name} written but not in manifest"
    assert f"toplevel: {direct.top}" in manifest


def test_control_false_stops_at_the_datapath(tmp_path):
    """The testbench form: no AXI4-Lite in front, coefficients on wires."""
    with_control = fusesoc.emit(MONO, tmp_path / "ctrl", verify=False)
    without = fusesoc.emit(MONO, tmp_path / "flat", control=False,
                           verify=False)
    assert "s_axi" in with_control["verilog"].read_text()
    assert "s_axi" not in without["verilog"].read_text()


def test_generator_protocol_end_to_end(tmp_path, monkeypatch):
    """Play FuseSoC's half: gapi input file in, pack in the work root out.

    The input is written as JSON, which is valid YAML -- the protocol
    reader accepts it either way, so this test does not depend on PyYAML
    being installed while real FuseSoC input (always YAML) does.
    """
    gapi = tmp_path / "input.yml"
    gapi.write_text(json.dumps({
        "gapi": "1.0",
        "vlnv": "lanserge:revela:mono_pack:0",
        "files_root": str(ROOT),
        "parameters": {"design": str(MONO.relative_to(ROOT)),
                       "verify": False},
    }))
    work = tmp_path / "work"
    work.mkdir()
    monkeypatch.chdir(work)
    assert fusesoc.main([str(gapi)]) == 0

    cores = list(work.glob("*.core"))
    assert len(cores) == 1
    manifest = cores[0].read_text()
    assert "name: lanserge:revela:mono_pack:0" in manifest
    assert "file_type: verilogSource" in manifest


def test_a_missing_design_parameter_is_refused(tmp_path, monkeypatch):
    gapi = tmp_path / "input.yml"
    gapi.write_text(json.dumps({"gapi": "1.0", "vlnv": "::x:0",
                                "files_root": str(ROOT), "parameters": {}}))
    monkeypatch.chdir(tmp_path)
    with pytest.raises(SystemExit, match="design"):
        fusesoc.main([str(gapi)])


# --------------------------------------------------------------------------- #
# The verify gate
# --------------------------------------------------------------------------- #

def _tiny_design() -> dict:
    """Two multiplying stages at 64x32: enough arithmetic to disagree,
    small enough that Verilator is a moment rather than a stage of CI."""
    from conftest import chain, describe
    return describe("tiny_pack", chain("blacklevel", "whitebalance"),
                    bit_depth=10)


def test_emit_verifies_by_default(tmp_path):
    """Emit IS the last gate before a hardware tool, so the twin runs
    unless a caller says it is proven elsewhere."""
    from conftest import verilator_available
    if not verilator_available():
        pytest.skip("verilator not on PATH")
    written = fusesoc.emit(_tiny_design(), tmp_path)
    assert written["verilog"].exists()


def test_cli_generate_emits_the_pack(tmp_path):
    """``revela generate`` is the command a hardware flow calls; it must
    speak the same emit, files landing where --out says."""
    from revela import cli

    design = tmp_path / "pipeline.json"
    design.write_text(json.dumps(_tiny_design()))
    out = tmp_path / "pack"
    assert cli.main(["generate", str(design), "--out", str(out),
                     "--no-verify"]) == 0
    assert (out / "tiny_pack.v").exists()
    assert (out / "tiny_pack_regmap.json").exists()


def test_a_diverging_twin_refuses_the_whole_pack(tmp_path, monkeypatch):
    """Refusal must come BEFORE anything is written: a pack that failed
    verification and still left artifacts behind is a stale-file trap.

    The RTL run is replaced wholesale, so this tests the refusal
    semantics without needing Verilator: any twin disagreement, however
    produced, must abort with nothing on disk.
    """
    from revela import run as runner

    monkeypatch.setattr(
        runner, "run_rtl",
        lambda chain, frame, values, context, bit_depth: frame ^ 1)
    out = tmp_path / "pack"
    with pytest.raises(ValueError, match="DIFFERS"):
        fusesoc.emit(_tiny_design(), out)
    assert not out.exists(), "a refused pack still wrote artifacts"
