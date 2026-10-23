# Copyright 2026 Serge Rabyking
# SPDX-License-Identifier: Apache-2.0 WITH SHL-2.1
"""depthview: millimetres to a smooth rainbow, red near, violet far."""
from __future__ import annotations

import colorsys

import numpy as np
import pytest

from conftest import requires_verilator, run_cocotb

from revela.blocks import depthview as module
from revela.blocks.depthview import depthview
from np2hw.blocks.stream import StreamSpec

WIDTH = 16
HEIGHT = 8
BIT_DEPTH = 16
SPEC = StreamSpec(bit_depth=BIT_DEPTH, channels=2)
DEFAULTS = {p.name: p.default for p in depthview.params.params}


def _view(depth, **values):
    """The view of a row of depths, the amplitude beside them unread."""
    depth = np.asarray(depth).reshape(1, -1)
    frame = np.stack([depth, np.full_like(depth, 1234)], axis=-1)
    return depthview.run(frame, values, bit_depth=BIT_DEPTH)[0].astype(int)


def test_the_key_colours_run_round_the_rainbow():
    """Red near to violet far, in order round the hue circle, each distinct
    from the next."""
    hues = [colorsys.rgb_to_hsv(*(c / 255 for c in k))[0]
            for k in module.KNOTS_RGB]
    assert hues[0] < 0.02 and 0.7 < hues[-1] < 0.85
    assert all(b > a + 0.03 for a, b in zip(hues, hues[1:]))


def test_what_was_not_measured_is_black():
    assert _view([0]).tolist() == [[0, 0, 0]]


def test_each_segment_starts_on_its_key_colour():
    """At the start of segment i the colour is key colour i exactly; before
    the range it is the first, and past it the end of the last segment."""
    start, shift = DEFAULTS["start"], DEFAULTS["shift"]
    seg = 1 << (module.FRAC_BITS + shift)              # millimetres a segment
    # a depth of 0 is "nothing measured": the first segment is read at 1 mm
    starts = [max(start + i * seg, 1)
              for i in range(len(module.KNOTS_RGB) - 1)]
    assert _view(starts).tolist() == [list(k) for k in module.KNOTS_RGB[:-1]]
    assert _view([1]).tolist() == [list(module.KNOTS_RGB[0])]
    last = _view([start + (module.SEGMENTS << (module.FRAC_BITS + shift))
                  + 999])[0]
    assert (np.abs(last - np.array(module.KNOTS_RGB[-1])) <= 2).all()


def test_the_rainbow_is_continuous():
    """Step by step along the range no channel moves by more than its
    segment's slope -- no jump anywhere, at the joins between segments
    either."""
    start, shift = DEFAULTS["start"], DEFAULTS["shift"]
    span = module.SEGMENTS << (module.FRAC_BITS + shift)
    rgb = _view(np.maximum(start + np.arange(span), 1))
    knots = np.array(module.KNOTS_RGB)
    worst = np.abs(np.diff(knots, axis=0)).max() / (1 << module.FRAC_BITS)
    assert np.abs(np.diff(rgb, axis=0)).max() <= int(worst) + 1


@pytest.mark.parametrize("shift", [0, 1, 3])
def test_no_bit_of_the_depth_is_dropped(shift):
    """However wide the range, every bit below the segment blends: in each
    segment, depths a millimetre apart -- differing in the lowest bit
    alone -- come out as different colours somewhere along it."""
    k = module.FRAC_BITS + shift
    for seg in range(module.SEGMENTS):
        depths = (seg << k) + np.arange(1 << k)
        depths = depths[depths > 0]
        rgb = _view(depths, start=0, shift=shift)
        half = len(rgb) // 2
        even = rgb[(depths & 1) == 0][:half]
        odd = rgb[(depths & 1) == 1][:half]
        assert (even != odd).any(), f"segment {seg} at shift {shift}"


@pytest.mark.verilog
@requires_verilator
def test_verilog_is_bit_exact_with_the_model(tmp_path, rng):
    """THE test for this block: ranges and starts in one elaboration, under
    randomised backpressure, with nothing measured, a depth before the
    range and one past it in each frame."""
    generated = depthview.generate(SPEC, WIDTH, HEIGHT,
                                   module_name="revela_depthview")
    settings = [{}, {"start": 0, "shift": 3}, {"start": 3000, "shift": 0},
                {"start": 65535, "shift": 7}]
    trials = []
    for overrides in settings:
        values = {**DEFAULTS, **overrides}
        depth = rng.integers(0, 6000, (HEIGHT, WIDTH))
        depth[0, :4] = (0, 1, values["start"], 65535)
        amp = rng.integers(0, 1 << 16, (HEIGHT, WIDTH))
        words = [[SPEC.pack((int(d), int(a))) for d, a in zip(dr, ar)]
                 for dr, ar in zip(depth, amp)]
        trials.append({
            "seed": int(rng.integers(0, 2**31)),
            "label": str(overrides or "reset"),
            "values": values,
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
