# Copyright 2026 Serge Rabyking
# SPDX-License-Identifier: Apache-2.0 WITH SHL-2.1
"""Where a context's value may come from: register, either, or stream only.

A crop window is software's choice and never a stream fact; a line length can
be either; a frame's position in the capture loop advances every frame, so no
register could ever hold it. These pin the rules that follow from that, and
the one that keeps the schema from drifting away from ``pipe``.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from revela.blocks import ContextBit, StreamPort, ispblock
from revela.blocks import pipe as pipe_block
from revela import compose
from revela.designs.composite import assemble

ROOT = Path(__file__).resolve().parent.parent
STREAM = {"bits": 12, "max_width": 16, "max_height": 8}


@ispblock(
    version=(1, 0),
    description="Test block: reads the frame's loop position.",
    inputs=(StreamPort("in"),),
    outputs=(StreamPort("out"),),
    consumes=("frame_phase",),
    context=(ContextBit("lo", "frame_phase", bit=0,
                        description="Low bit of the loop position"),),
)
def phased(pixel, p, ctx, bit_depth):
    """The smallest model that needs the loop position."""
    return pixel + ctx.lo


def _phased(context_from_stream):
    return assemble("t", {"phased": phased},
                    [("in", "phased.in"), ("phased.out", "out")],
                    stream=dict(STREAM, context=list(context_from_stream)))


def test_the_schema_admits_exactly_the_contexts_a_stream_may_carry():
    """The schema's enum is how a static JSON file states the policy, so it
    must say what ``pipe`` says -- a second list kept by hand is the drift
    ``run.py`` once had."""
    schema = json.loads((ROOT / "revela/designs/blocks.schema.json").read_text())
    port = schema["$defs"]["ports"]["items"]["properties"]
    enum = port["context"]["items"]["enum"]
    assert sorted(enum) == sorted(c.name for c in pipe_block.CONTEXT
                                  if c.origin != "register")


def test_a_stream_only_context_takes_no_register_anywhere():
    """No pipeline gets a register for ``frame_phase`` -- which is why adding
    it moved no address in any existing design."""
    registers = {p.name for p in pipe_block.pipe.params.params}
    assert "frame_phase" not in registers
    assert {c.name for c in pipe_block.CONTEXT if c.origin != "stream"} <= registers


def test_consuming_it_without_the_stream_is_refused():
    with pytest.raises(ValueError, match="must arrive with the stream"):
        compose.stages(_phased(()))


def test_a_register_only_context_cannot_arrive_with_the_stream():
    design = assemble("t", {}, [("in", "out")],
                      stream=dict(STREAM, context=["window_x0"]))
    with pytest.raises(ValueError, match="software's choice"):
        compose.shape(design)


def test_where_the_stream_delivers_it_the_pipeline_has_it():
    """Listed in ``stream.context``, the pipeline HAS the signal: a port, no
    register, and ``commit`` exactly where it was before the signal existed."""
    pipeline = _phased(("frame_phase",))
    assert "frame_phase" in {c.name for c in compose.contexts(pipeline)}
    generated = compose.generate(pipeline, control=True)
    assert "ctx_frame_phase" in dict(generated.modules)[generated.top]
    pipe = next(b for b in compose.register_map(pipeline)["blocks"]
                if b["path"] == "pipe")
    offsets = {r["name"]: r["offset"] for r in pipe["registers"]}
    assert "frame_phase" not in offsets
    assert offsets["commit"] == 0x24


def test_elsewhere_the_pipeline_does_not_have_it():
    design = assemble("t", {}, [("in", "out")], stream=STREAM)
    assert "frame_phase" not in {c.name for c in compose.contexts(design)}
