# Copyright 2026 Serge Rabyking
# SPDX-License-Identifier: Apache-2.0 WITH SHL-2.1
"""The time-of-flight chain, composed: phase to depth, as one pipeline.

Each block is bit-exact with its own Verilog in its own test. This composes
them the way a design does -- ``tofquad`` into ``tofrange`` -- and holds the
generated pipeline to the NumPy chain through the RTL twin, at every phase
alignment and in a scrambled phase order. The eight-channel input -- four
frames' samples and their phase tags -- stands in for the frame history that
will produce it.
"""
from __future__ import annotations

import numpy as np
import pytest

from conftest import requires_verilator

from revela.blocks.tofquad import tofquad
from revela.blocks.tofrange import tofrange
from revela.designs.composite import assemble
from revela.run import pixel_chain, run_model, run_rtl
from revela.stream import StreamSpec

WIDTH = 16
HEIGHT = 8
BIT_DEPTH = 16
OFFSET = 1 << 15


def _pipeline():
    return assemble(
        "tof", {"quad": tofquad, "range": tofrange},
        [("in", "quad.in"), ("quad.out", "range.in"), ("range.out", "out")],
        stream={"bits": BIT_DEPTH, "max_width": WIDTH, "max_height": HEIGHT,
                "channels": 8, "context": ["frame_phase"]})


def _window(rng, pos, order=(0, 1, 2, 3)):
    """A frame history's word: channel k is the sample from frame n-k, which
    sits at loop position (pos - k) mod 4 and was captured at phase
    order[position]; channel 4+k is that phase. Correlations of a plausible
    size, bright enough that most pixels clear the confidence gate."""
    planes = rng.integers(-2400, 2400, (4, HEIGHT, WIDTH))
    phase = [order[(pos - k) % 4] for k in range(4)]
    return np.stack([planes[ph] + OFFSET for ph in phase]
                    + [np.full((HEIGHT, WIDTH), ph) for ph in phase], axis=-1)


def test_the_chain_composes():
    chain = pixel_chain(_pipeline())
    assert [stage.path for stage in chain] == ["quad", "range"]


@pytest.mark.verilog
@requires_verilator
@pytest.mark.parametrize("pos, order", [(0, (0, 1, 2, 3)), (1, (0, 1, 2, 3)),
                                        (2, (0, 1, 2, 3)), (3, (0, 1, 2, 3)),
                                        (1, (0, 2, 1, 3))])
def test_the_generated_chain_is_bit_exact_with_the_models(rng, pos, order):
    """Phase to depth through generated Verilog equals the NumPy chain, at
    every alignment and in any phase order -- the whole ISP side of
    time-of-flight, as hardware."""
    chain = pixel_chain(_pipeline())
    values = {"quad": {},
              "range": {p.name: p.default for p in tofrange.params.params}}
    context = {}
    frame = _window(rng, pos, order)

    model = run_model(chain, frame, values, context, BIT_DEPTH)
    rtl = run_rtl(chain, frame, values, context, BIT_DEPTH)
    np.testing.assert_array_equal(rtl, model)
    assert model[..., 0].any(), "the gate zeroed everything; the test proves nothing"
