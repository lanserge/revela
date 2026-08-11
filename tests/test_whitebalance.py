# Copyright 2026 Serge Rabyking
# SPDX-License-Identifier: Apache-2.0 WITH SHL-2.1
"""White balance: the NumPy model, and its bit-exact agreement with the Verilog.

Rule 3, same shape as blacklevel's suite: the simulator half lives in
``tb_whitebalance.py``; this module builds the design, chooses stimulus that
exercises truncation and saturation deliberately, and covers the pure-Python
behaviour.
"""
from __future__ import annotations

import numpy as np
import pytest

from conftest import raw_frame, requires_verilator, run_cocotb

from revela.blocks import whitebalance
from revela.blocks.whitebalance import GAIN_ONE
from revela.stream import StreamSpec

GAIN = whitebalance.whitebalance.params.declaration("gain")

WIDTH = 16
HEIGHT = 8
BIT_DEPTH = 12


# --------------------------------------------------------------------------- #
# The model
# --------------------------------------------------------------------------- #

def test_unity_gain_is_pass_through(rng):
    """Reset values must not change the image.

    An unconfigured pipeline should show the illuminant's cast, not a wrong
    image -- same bring-up argument as blacklevel's zero offset.
    """
    frame = raw_frame(rng, WIDTH, HEIGHT, BIT_DEPTH)
    out = whitebalance.whitebalance.run(frame, {}, bayer_phase=0, bit_depth=BIT_DEPTH)
    np.testing.assert_array_equal(out, frame)


def test_truncation_is_a_floor_not_a_round():
    """(3 * 1.5) is 4, not 5: the shift truncates, and the model says so.

    This is THE fixed-point decision a float reference would silently get
    wrong -- round-to-nearest reads plausibly and matches nothing the
    hardware does.
    """
    frame = np.full((2, 2), 3, dtype=np.uint16)
    gains = GAIN.values(np.full((2, 2), GAIN_ONE + GAIN_ONE // 2))
    assert whitebalance.whitebalance.run(
        frame, gains, bit_depth=BIT_DEPTH).tolist() == [[4, 4], [4, 4]]


def test_saturates_at_full_scale_and_holds_zero():
    top = (1 << BIT_DEPTH) - 1
    frame = np.array([[0, top], [4000, 1]], dtype=np.uint16)
    gains = GAIN.values(np.full((2, 2), 4 * GAIN_ONE))
    assert whitebalance.whitebalance.run(
        frame, gains, bit_depth=BIT_DEPTH).tolist() == [
        [0, top], [top, 4]]


def test_full_scale_survives_unity_exactly():
    """top * 256 >> 8 == top: no width lost anywhere in the multiply path."""
    top = (1 << BIT_DEPTH) - 1
    mid = 1 << (BIT_DEPTH - 1)
    frame = np.array([[top, mid], [mid - 1, top - 1]], dtype=np.uint16)
    out = whitebalance.whitebalance.run(frame, {}, bayer_phase=0, bit_depth=BIT_DEPTH)
    np.testing.assert_array_equal(out, frame)


@pytest.mark.parametrize("phase", [0, 1, 2, 3])
def test_gain_follows_the_colour_not_the_position(phase):
    """One bitstream, every orientation -- the register moves with the colour."""
    frame = np.full((2, 2), 64, dtype=np.uint16)
    gains = {"gain_0_0": 1 * GAIN_ONE, "gain_0_1": 2 * GAIN_ONE,
             "gain_1_0": 3 * GAIN_ONE, "gain_1_1": 4 * GAIN_ONE}
    out = whitebalance.whitebalance.run(frame, gains, bayer_phase=phase, bit_depth=BIT_DEPTH)

    phase_row, phase_col = (phase >> 1) & 1, phase & 1
    for y in (0, 1):
        for x in (0, 1):
            colour = ((y ^ phase_row), (x ^ phase_col))
            factor = gains[f"gain_{colour[0]}_{colour[1]}"] // GAIN_ONE
            assert out[y, x] == 64 * factor
    assert sorted(out.ravel().tolist()) == [64, 128, 192, 256]


def test_odd_dimensions_are_rejected():
    with pytest.raises(ValueError, match="odd dimension"):
        whitebalance.whitebalance.run(np.zeros((4, 5), dtype=np.uint16))


def test_registers_from_gains_encodes_the_green_convention():
    """Three loop colours -> four registers, greens equal, exactly once."""
    assert whitebalance.registers_from_gains(r=189, b=338) == {
        "gain_0_0": 189, "gain_0_1": 256, "gain_1_0": 256, "gain_1_1": 338}
    with pytest.raises(ValueError, match="outside"):
        whitebalance.registers_from_gains(r=1 << 16, b=256)


def test_a_multi_channel_stream_fails_at_the_trace():
    """No meaning tags: the arithmetic is the contract. This block eats one
    sample per pixel; a packed multi-channel word has no phase slices and
    no plain samples to offset, so the trace itself dies at compose time,
    before any Verilog exists."""
    with pytest.raises(Exception):
        whitebalance.whitebalance.generate(
            StreamSpec(bit_depth=BIT_DEPTH, channels=3), 8, 8,
            module_name="revela_whitebalance")


# --------------------------------------------------------------------------- #
# Rule 3: bit-exact model versus generated Verilog
# --------------------------------------------------------------------------- #

@pytest.mark.verilog
@requires_verilator
def test_verilog_is_bit_exact_with_the_model(tmp_path, rng):
    """THE test for this block.

    Four trials in one elaboration, one per CFA phase, gains spanning
    attenuation (< unity), unity, non-integer factors that force the
    truncation, and saturating factors -- under randomised backpressure.
    """
    spec = StreamSpec(bit_depth=BIT_DEPTH)
    generated = whitebalance.whitebalance.generate(
        spec, WIDTH, HEIGHT, module_name="revela_whitebalance")

    trials = []
    for phase in range(4):
        frame = raw_frame(rng, WIDTH, HEIGHT, BIT_DEPTH)
        gains = GAIN.values(rng.integers(64, 1025, (2, 2)))   # x0.25 .. x4
        trials.append({
            "seed": int(rng.integers(0, 2**31)),
            "context": {"bayer_phase": phase},
            "values": gains,
            "frame": frame.ravel().tolist(),
        })

    run_cocotb(
        tmp_path=tmp_path,
        verilog=generated.verilog,
        toplevel=generated.top,
        test_module="tb_block",
        case={
            "block": "whitebalance",
            "width": WIDTH,
            "height": HEIGHT,
            "bit_depth": BIT_DEPTH,
            "trials": trials,
        },
    )
