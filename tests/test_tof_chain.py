# Copyright 2026 Serge Rabyking
# SPDX-License-Identifier: Apache-2.0 WITH SHL-2.1
"""The time-of-flight chain, composed: phase to depth, as one pipeline.

Each block is bit-exact with its own Verilog in its own test. This composes
them the way a design does -- ``tofquad``, reading the stream's history,
into ``tofrange`` -- and holds the generated pipeline to the NumPy chain
through the RTL twin over a run of phase frames sent back to back, the
loop place bound to the frame count each frame's header carries, from every
alignment and in a scrambled phase order.
"""
from __future__ import annotations

import numpy as np
import pytest

from conftest import requires_verilator

from revela.blocks.pipe import VOCABULARY
from revela.blocks.tofquad import tofquad
from revela.blocks.tofrange import tofrange
from np2hw.design.composite import assemble
from np2hw.pipeline.run import pixel_chain, run_model, run_rtl
from np2hw.blocks.stream import StreamSpec

WIDTH = 16
HEIGHT = 8
BIT_DEPTH = 16
OFFSET = 1 << 15


def _pipeline():
    return assemble(
        "tof", {"quad": tofquad.bind({"pos": "frame_seq & 3"}, VOCABULARY),
                "range": tofrange},
        [("in", "quad.in"), ("quad.out", "range.in"), ("range.out", "out")],
        vocabulary=VOCABULARY,
        stream={"bits": BIT_DEPTH, "max_width": WIDTH, "max_height": HEIGHT,
                "context": ["frame_seq"]})


def _run(rng, first, order, count=9):
    """A ToF sensor's frames from count ``first`` on: frame n carries the
    phase of its place, ``order[(first + n) & 3]``, one static scene's.
    Correlations of a plausible size, bright enough that most pixels clear
    the confidence gate."""
    planes = rng.integers(-2400, 2400, (4, HEIGHT, WIDTH))
    seq = [first + n for n in range(count)]
    return (np.stack([planes[order[q & 3]] + OFFSET for q in seq]), seq)


def test_the_chain_composes():
    chain = pixel_chain(_pipeline())
    assert [stage.path for stage in chain] == ["quad", "range"]


@pytest.mark.verilog
@requires_verilator
@pytest.mark.parametrize("first, order", [(0, (0, 1, 2, 3)), (5, (0, 1, 2, 3)),
                                          (10, (0, 1, 2, 3)), (7, (0, 2, 1, 3))])
def test_the_generated_chain_is_bit_exact_with_the_models(rng, first, order):
    """Phase to depth through generated Verilog equals the NumPy chain over
    a run, every frame's alignment its own, in any phase order -- the whole
    ISP side of time-of-flight, as hardware."""
    chain = pixel_chain(_pipeline())
    values = {"quad": {f"tag_{i}": order[i] for i in range(4)},
              "range": {p.name: p.default for p in tofrange.params.params}}
    video, seq = _run(rng, first, order)
    context = {"frame_seq": seq}

    model = run_model(chain, video, values, context, BIT_DEPTH)
    assert len(model) == len(video) - 3
    rtl = run_rtl(chain, video, values, context, BIT_DEPTH)
    np.testing.assert_array_equal(rtl, model)
    assert model[..., 0].any(), "the gate zeroed everything; the test proves nothing"
