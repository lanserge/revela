# Copyright 2026 Serge Rabyking
# SPDX-License-Identifier: Apache-2.0 WITH SHL-2.1
"""depthview: millimetres to a red-green-blue ramp a display shows."""
from __future__ import annotations

import numpy as np
import pytest

from conftest import requires_verilator, run_cocotb

from revela.blocks.depthview import depthview
from np2hw.blocks.stream import StreamSpec

WIDTH = 16
HEIGHT = 8
BIT_DEPTH = 16
SPEC = StreamSpec(bit_depth=BIT_DEPTH, channels=2)


def _view(depth, shift=3):
    """The view of a row of depths, the amplitude beside them unread."""
    depth = np.asarray(depth).reshape(1, -1)
    frame = np.stack([depth, np.full_like(depth, 1234)], axis=-1)
    return depthview.run(frame, {"shift": shift}, bit_depth=BIT_DEPTH)[0]


def test_near_is_red_far_is_blue_and_green_between():
    """At 8 mm a step: 8 mm is all but pure red, 2048 mm pure green, the
    range's end pure blue."""
    rgb = _view([8, 2048, 4088])
    assert rgb.tolist() == [[254, 1, 0], [0, 255, 0], [0, 0, 255]]


def test_the_ramp_is_continuous_and_of_one_brightness():
    """Each step moves one level and keeps R + G + B at 255: no jump where
    the segments meet, and no part of the range brighter than another."""
    rgb = _view(np.arange(1, 512) << 3).astype(int)
    assert (rgb.sum(axis=1) == 255).all()
    assert (np.abs(np.diff(rgb, axis=0)).sum(axis=1) <= 2).all()


def test_what_was_not_measured_is_black_and_what_is_past_the_range_blue():
    rgb = _view([0, 5000, 65535])
    assert rgb.tolist() == [[0, 0, 0], [0, 0, 255], [0, 0, 255]]


@pytest.mark.parametrize("shift, span", [(1, 1024), (2, 2048), (3, 4096)])
def test_the_shift_sets_the_range(shift, span):
    """The ramp spans 512 << shift millimetres: its middle is green."""
    assert _view([span // 2], shift).tolist() == [[0, 255, 0]]
    assert _view([span - (1 << shift)], shift).tolist() == [[0, 0, 255]]


@pytest.mark.verilog
@requires_verilator
def test_verilog_is_bit_exact_with_the_model(tmp_path, rng):
    """THE test for this block: every range in one elaboration, under
    randomised backpressure, with nothing measured and the ramp's ends
    placed in each frame."""
    generated = depthview.generate(SPEC, WIDTH, HEIGHT,
                                   module_name="revela_depthview")
    trials = []
    for shift in range(8):
        depth = rng.integers(0, 6000, (HEIGHT, WIDTH))
        depth[0, :4] = (0, 1, (256 << shift) - 1, 65535)
        amp = rng.integers(0, 1 << 16, (HEIGHT, WIDTH))
        words = [[SPEC.pack((int(d), int(a))) for d, a in zip(dr, ar)]
                 for dr, ar in zip(depth, amp)]
        trials.append({
            "seed": int(rng.integers(0, 2**31)),
            "label": f"shift {shift}",
            "values": {"shift": shift},
            "frame": [w for row in words for w in row],
        })
    run_cocotb(
        tmp_path=tmp_path,
        verilog=generated.verilog,
        toplevel=generated.top,
        test_module="np2hw.testing.block",
        case={
            "block": "depthview",
            "width": WIDTH,
            "height": HEIGHT,
            "bit_depth": BIT_DEPTH,
            "in_channels": 2,
            "trials": trials,
        },
    )
