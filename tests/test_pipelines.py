# Copyright 2026 Serge Rabyking
# SPDX-License-Identifier: Apache-2.0 WITH SHL-2.1
"""The shipped pipelines: compositions of blocks in revela.pipelines, one
to a file -- the standard ISP, and designs built of it.

There is one standard ISP, revela.pipelines.bayer_isp, and every design --
these, and the board's -- is built of it rather than spelling a chain out
beside it. An ISP is sensor-independent: what it is built for is the stream
entering it -- a depth, a widest and a tallest frame -- and a sensor's
tuning is a profile, which lives with the sensor.
"""
import json
from pathlib import Path

import pytest

import revela
from np2hw.pipeline import compose

from np2hw import pipeline as pipelines
from np2hw.design import composite

FOLDER = Path(revela.__file__).parent / "pipelines"
# Two kinds of file: a design, holding the top revela_<file>, and a standard
# ISP, named after its file and built for whatever stream its user sets.
FILES = sorted(p for p in FOLDER.glob("*.json")
               if f"revela_{p.stem}" in json.loads(p.read_text())["blocks"])
STANDARD = "revela.pipelines.bayer_isp:bayer_isp"
BOARD = "revela.boards.pynq_z2.isp:revela_isp_core"


def ref(path: Path) -> str:
    return f"revela.pipelines.{path.stem}:revela_{path.stem}"


def test_there_are_pipelines():
    assert FILES, f"no pipelines in {FOLDER}"


@pytest.mark.parametrize("path", FILES, ids=lambda p: p.stem)
def test_a_pipeline_generates(path):
    generated = compose.generate(pipelines.load(ref(path)))
    assert generated.verilog.strip().endswith("endmodule")
    assert generated.top in generated.module_names()


@pytest.mark.parametrize("path", FILES, ids=lambda p: p.stem)
def test_a_pipeline_is_named_after_its_file(path):
    """The block is the top module: revela_<file>, unique side by side and
    clear of anything in the integrator's own Verilog."""
    assert f"revela_{path.stem}" in json.loads(path.read_text())["blocks"]
    assert pipelines.load(ref(path)).name == f"revela_{path.stem}"


@pytest.mark.parametrize("design", [*map(ref, FILES), BOARD])
def test_every_design_is_built_of_the_standard_isp(design):
    """One standard ISP, reused: a design that spelled its chain out beside
    bayer_isp would be a second description of it, free to drift."""
    loaded = composite.load(design)
    assert STANDARD in {composite.canonical(r)
                        for r in loaded.composites.values()}


@pytest.mark.parametrize("design", [*map(ref, FILES), BOARD])
def test_a_design_is_built_for_a_stream_and_names_no_sensor(design):
    """Depth, widest and tallest frame, on every input: the whole of what
    the ISP is built for. The build reads no sensor."""
    loaded = composite.load(design)
    for port in loaded.inputs:
        assert {"bits", "max_width", "max_height"} <= set(
            loaded.streams[port]), port


def test_stereo_is_the_standard_isp_twice():
    """Two eyes, one module per block instantiated per eye -- and each eye
    keeps its own registers, so each is tuned on its own."""
    pipeline = pipelines.load(ref(FOLDER / "stereo.json"))
    modules = compose.generate(pipeline).module_names()
    assert "revela_bayer_isp_ccm" in modules
    assert not any(m.startswith(("revela_left_", "revela_right_"))
                   for m in modules)
    from np2hw.design.composite import _shared_subsystems
    assert sorted(_shared_subsystems(pipeline)) == ["left", "right"]
    assert compose.stage(pipeline, "left.ccm").instance.base != \
        compose.stage(pipeline, "right.ccm").instance.base


def test_a_composite_used_once_is_spelled_out():
    """Wrapping a chain in a block does not change a design's RTL."""
    from np2hw.design.composite import _shared_subsystems
    assert not _shared_subsystems(pipelines.load(BOARD))
