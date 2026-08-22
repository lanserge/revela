# Copyright 2026 Serge Rabyking
# SPDX-License-Identifier: Apache-2.0 WITH SHL-2.1
"""Depth travels the chain the way the channel count does.

A block is told the depth of the word it is GIVEN, read off its driver's
trace, not the depth the pipeline was declared with. The two coincide for
a chain whose every stage saturates back -- which is every chain shipped
today -- so the first test here is the one that says this changed
nothing. The rest say what it makes possible.
"""
from __future__ import annotations

import numpy as np
import pytest

from np2hw import saturate

from revela.blocks import StreamPort, ispblock, registry
from revela.compose import Pipeline
from revela.stream import StreamSpec

WIDTH, HEIGHT = 32, 16


def _chain(pipeline, names_and_blocks):
    """Wire a straight chain from the pipeline input to its output."""
    source = "in"
    for name, block in names_and_blocks:
        pipeline.add(name, block)
        pipeline.connect(source, f"{name}.{block.ports.inputs[0]}")
        source = f"{name}.{block.ports.outputs[0]}"
    pipeline.connect(source, "out")


def _depths(pipeline):
    """The (channels, bit_depth) each stage is told, in datapath order."""
    seen, built = {}, {}
    for stage in pipeline.datapath:
        channels, depth = pipeline._incoming_stream(stage, built)
        result = stage.block.generate(
            pipeline.spec.with_stream(channels, depth), WIDTH, HEIGHT,
            module_name=f"probe_{stage.module_prefix}")
        built[stage.path] = result
        seen[stage.path] = (channels, depth)
    return seen, built


# --------------------------------------------------------------------------- #
# The no-op: the shipped chain is unchanged
# --------------------------------------------------------------------------- #

def test_a_saturating_chain_is_told_the_pipeline_depth_at_every_stage():
    """Every shipped block saturates back, so threading changes nothing.

    This is the test that had to pass before the threading was worth
    having: the demo pipeline's generated Verilog is byte-identical
    across the change, and this says why.
    """
    reg = registry()
    spec = StreamSpec(bit_depth=10, channels=1)
    p = Pipeline("saturating", spec, WIDTH, HEIGHT,
                 inputs=("in",), outputs=("out",))
    _chain(p, [("bl", reg["blacklevel"]),
               ("wb", reg["whitebalance"]),
               ("hg", reg["ha_green"]),
               ("hr", reg["ha_rb"]),
               ("cc", reg["ccm"])])
    told, _ = _depths(p)
    assert {path: depth for path, (_ch, depth) in told.items()} == {
        "bl": 10, "wb": 10, "hg": 10, "hr": 10, "cc": 10}


def test_channels_still_thread_from_the_trace():
    """The channel count keeps arriving from the driver, not a declaration."""
    reg = registry()
    p = Pipeline("channels", StreamSpec(bit_depth=10, channels=1),
                 WIDTH, HEIGHT, inputs=("in",), outputs=("out",))
    _chain(p, [("hg", reg["ha_green"]), ("hr", reg["ha_rb"]),
               ("cc", reg["ccm"])])
    told, _ = _depths(p)
    assert {path: ch for path, (ch, _d) in told.items()} == {
        "hg": 1, "hr": 2, "cc": 3}


# --------------------------------------------------------------------------- #
# What threading makes possible
# --------------------------------------------------------------------------- #

@ispblock(
    version=(1, 0),
    description="Adds a pixel to itself without bounding it: one bit wider.",
    inputs=(StreamPort("in", "Any single-component stream."),),
    outputs=(StreamPort("out", "The same value, one bit taller."),),
)
def widen(pixel, p, ctx, bit_depth: int):
    return (pixel.astype(np.int32) * 2).astype(np.uint32)


@ispblock(
    version=(1, 0),
    description="Saturates to eight bits, whatever it was given.",
    inputs=(StreamPort("in", "Any single-component stream."),),
    outputs=(StreamPort("out", "Eight bits."),),
)
def narrow(pixel, p, ctx, bit_depth: int):
    return saturate(pixel.astype(np.int32), 8).astype(np.uint16)


def test_a_widening_block_hands_on_the_wider_depth():
    """bit_depth + 1 out of one stage is bit_depth + 1 into the next."""
    p = Pipeline("widening", StreamSpec(bit_depth=10, channels=1),
                 WIDTH, HEIGHT, inputs=("in",), outputs=("out",))
    _chain(p, [("a", widen), ("b", widen), ("c", widen)])
    told, _ = _depths(p)
    assert [depth for _ch, depth in
            (told[k] for k in ("a", "b", "c"))] == [10, 11, 12]


def test_a_narrowing_block_hands_on_the_narrower_depth():
    """The 'eight bits after gamma' the stream docstring describes."""
    p = Pipeline("narrowing", StreamSpec(bit_depth=12, channels=1),
                 WIDTH, HEIGHT, inputs=("in",), outputs=("out",))
    _chain(p, [("a", widen), ("b", narrow), ("c", widen)])
    told, _ = _depths(p)
    assert [depth for _ch, depth in
            (told[k] for k in ("a", "b", "c"))] == [12, 13, 8]


def test_the_pipeline_output_port_carries_the_traced_width():
    """The exit boundary is the last block's word, not the declared spec."""
    p = Pipeline("widening_out", StreamSpec(bit_depth=10, channels=1),
                 WIDTH, HEIGHT, inputs=("in",), outputs=("out",))
    _chain(p, [("a", widen), ("b", widen)])
    _told, built = _depths(p)
    assert p._output_stream(built, "out").data_bits == 12
    # and the entry stays the one declared fact
    assert p.spec.data_bits == 10


def test_a_widening_chain_composes():
    """np2hw refuses mismatched widths, so this only passes if they agree."""
    p = Pipeline("widening_whole", StreamSpec(bit_depth=10, channels=1),
                 WIDTH, HEIGHT, inputs=("in",), outputs=("out",))
    _chain(p, [("a", widen), ("b", widen)])
    generated = p.generate(control=False)
    assert generated.modules


def test_the_composer_refuses_when_a_consumer_disagrees(monkeypatch):
    """The safety net under all of this: a width mismatch is not silent.

    Threading is what keeps producer and consumer agreeing. Break the
    threading and np2hw's own check must still catch it, so a future
    change here fails loudly rather than emitting a wrong bus.
    """
    p = Pipeline("mismatch", StreamSpec(bit_depth=10, channels=1),
                 WIDTH, HEIGHT, inputs=("in",), outputs=("out",))
    _chain(p, [("a", widen), ("b", widen)])
    # pretend depth never threaded: every block told the pipeline's depth
    monkeypatch.setattr(Pipeline, "_incoming_stream",
                        lambda self, stage, built:
                        (self.spec.channels, self.spec.bit_depth))
    with pytest.raises(Exception) as caught:
        p.generate(control=False)
    assert "bit" in str(caught.value).lower()


def test_geometry_is_threaded_from_the_driver_not_broadcast():
    """A block is built for the frame it is GIVEN.

    Depth and channels have always been read from the driver's traced
    interface; geometry was broadcast from the pipeline instead, and was
    right only because every block preserves geometry -- revela's stencils
    pad, so a 5x5 window emits what it consumed. A fact with two owners
    that agree by construction still has two owners, and the first block
    that changes size makes them disagree.
    """
    import io
    from contextlib import redirect_stdout

    from conftest import chain, describe
    from revela import designs

    pipeline = designs.build(describe(
        "geo", chain("blacklevel", "ha_green", "ha_rb"),
        bit_depth=10, width=64, height=32))

    built, seen = {}, []
    for stage in pipeline.datapath:
        width, height = pipeline._incoming_geometry(stage, built)
        channels, depth = pipeline._incoming_stream(stage, built)
        with redirect_stdout(io.StringIO()):
            result = stage.block.generate(
                pipeline.spec.with_stream(channels, depth), width, height,
                module_name=f"t_{stage.path}")
        built[stage.path] = result
        output = result.core["interface"].get("output") or {}
        seen.append((stage.path, (width, height),
                     (output.get("cols"), output.get("rows"))))

    # every block is told a real geometry, and it is the one its driver
    # produced -- not the pipeline's, which merely coincides today
    for index, (path, given, produced) in enumerate(seen):
        assert given == (64, 32), f"{path} was built for {given}"
        assert produced == (64, 32), f"{path} produced {produced}"
        if index:
            assert given == seen[index - 1][2], (
                f"{path} was built for {given} but its driver produced "
                f"{seen[index - 1][2]}")
