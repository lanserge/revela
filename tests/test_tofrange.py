# Copyright 2026 Serge Rabyking
# SPDX-License-Identifier: Apache-2.0 WITH SHL-2.1
"""Time-of-flight range: the NumPy model, and its bit-exact Verilog.

Rule 3: the only per-block correctness test is bit-exact agreement between the
model and the generated Verilog. The model tests pin the baked constants and
the conventions a depth consumer relies on.
"""
from __future__ import annotations

import math

import numpy as np
import pytest

from conftest import requires_verilator, run_cocotb

from revela.blocks import tofrange as module
from revela.blocks.tofrange import tofrange
from np2hw.blocks.stream import StreamSpec

WIDTH = 16
HEIGHT = 8
BIT_DEPTH = 16
SPEC = StreamSpec(bit_depth=BIT_DEPTH, channels=2)


def test_the_rsqrt_table_is_the_formula():
    """The baked table is what its comment says: ``2**14 / sqrt(1 + i/16)``."""
    scale = 1 << module.COS_BITS
    assert module.RSQRT == [round(scale / math.sqrt(1 + i / module.RSQRT_KNOTS))
                            for i in range(module.RSQRT_KNOTS + 1)]


def test_the_intrinsic_defaults_are_the_measured_lens():
    """Reciprocal focal lengths at 18 fractional bits, from fx 190.92, fy 191.25."""
    defaults = {p.name: p.default for p in tofrange.params.params}
    assert defaults["inv_fx"] == round((1 << module.INTR_FRAC) / 190.92)
    assert defaults["inv_fy"] == round((1 << module.INTR_FRAC) / 191.25)


def test_the_optical_centre_is_not_foreshortened():
    """At the principal point radial range IS depth: cos(theta) = 1 exactly."""
    phase = np.zeros((180, 240, 2), dtype=np.int64)
    phase[..., 0] = 20000
    phase[..., 1] = 1000                          # well above amp_min
    out = tofrange.run(phase, {}, bit_depth=16)
    radial = (((48814 - 20000) & 0xFFFF) * 4123) >> 16
    assert out[90, 120, 0] == radial
    assert out[0, 0, 0] < radial                  # a corner is foreshortened


def test_every_distance_in_one_turn_is_itself():
    """The circle is cut where the distance is zero, a guard band before
    it: across the whole turn the distance runs from 0 up to the window's
    end, never negative, continuous where a cut at phase 0 would have put
    the jump; the guard band reads as the nearest distance; and the only
    jumps are its edges."""
    defaults = {p.name: p.default for p in tofrange.params.params}
    zero, span, guard = (defaults[k] for k in ("zero", "span", "guard"))
    turn = 1 << 16
    phase = np.zeros((1, turn, 2), dtype=np.int64)
    phase[0, :, 0] = np.arange(turn)
    phase[..., 1] = 1000
    # no angle off the axis anywhere, so radial range is depth
    out = tofrange.run(phase, {"inv_fx": 0, "inv_fy": 0}, bit_depth=16)[0, :, 0]
    # walk the turn outwards from zero, the way the sensor runs it
    step = -1 if span < 0 else 1
    walk = out[(zero + step * np.arange(turn)) & 0xFFFF].astype(np.int64)
    assert walk[0] == 0                       # zero distance itself
    window, band = walk[:turn - guard], walk[turn - guard:]
    assert (band == 1).all()                  # just short of zero: nearest
    assert window.max() < abs(span) * (turn - guard) / turn + 1
    assert (np.diff(window) >= 0).all() and (np.diff(window) <= 1).all()
    assert (out >= 0).all()


def test_a_hand_at_the_lens_reads_as_near():
    """Readings a little below zero -- a hand at the lens read 21 to 24 mm
    short -- are the nearest distance, not the far end of the turn."""
    defaults = {p.name: p.default for p in tofrange.params.params}
    zero, span = defaults["zero"], defaults["span"]
    short = round(24 * (1 << 16) / abs(span))          # 24 mm, in phase
    phase = np.zeros((180, 240, 2), dtype=np.int64)
    phase[..., 0] = (zero + (short if span < 0 else -short)) & 0xFFFF
    phase[..., 1] = 30000
    out = tofrange.run(phase, {}, bit_depth=16)[..., 0]
    assert (out == 1).all()


def test_too_little_light_reads_as_no_measurement():
    """Below amp_min the depth is zero -- the consumer's word for invalid."""
    phase = np.zeros((4, 4, 2), dtype=np.int64)
    phase[..., 0] = 20000
    phase[..., 1] = 10                            # below the default 25
    assert not tofrange.run(phase, {}, bit_depth=16)[..., 0].any()


# --------------------------------------------------------------------------- #
# Rule 3: bit-exact model versus generated Verilog
# --------------------------------------------------------------------------- #

@pytest.mark.verilog
@requires_verilator
def test_verilog_is_bit_exact_with_the_model(tmp_path, rng):
    """THE test for this block.

    Trials with different registers in one elaboration, under randomised
    backpressure: the reset calibration; a principal point off the frame and
    reciprocal focal lengths large enough to push the curve's argument past
    its table, so the clamp is exercised; a span running forwards and a
    zero elsewhere on the circle, so both directions and the wrap are; and
    an amplitude threshold in the middle of the range, so both sides of the
    confidence gate are.
    """
    generated = tofrange.generate(SPEC, WIDTH, HEIGHT,
                                  module_name="revela_tofrange")
    settings = [
        {},
        {"cx": -30, "cy": 40, "inv_fx": 9000, "inv_fy": 7000},
        {"span": 90000, "zero": 12345, "guard": 9000},
        {"amp_min": 30000},
    ]
    # The testbench drives only the registers a trial names, so every trial
    # starts from the full declared reset and overrides from there -- a
    # register left undriven is not the declared default, it is zero.
    reset = {p.name: p.default for p in tofrange.params.params}
    trials = []
    for overrides in settings:
        values = {**reset, **overrides}
        phase = rng.integers(0, 1 << 16, (HEIGHT, WIDTH))
        amp = rng.integers(0, 1 << 16, (HEIGHT, WIDTH))
        amp[0, :4] = (0, 25, 29999, 30000)          # either side of a gate
        words = [[SPEC.pack((int(ph), int(am))) for ph, am in zip(pr, ar)]
                 for pr, ar in zip(phase, amp)]
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
            "block": "tofrange",
            "width": WIDTH,
            "height": HEIGHT,
            "bit_depth": BIT_DEPTH,
            "in_channels": 2,
            "trials": trials,
        },
    )
