# Copyright 2026 Serge Rabyking
# SPDX-License-Identifier: Apache-2.0 WITH SHL-2.1
"""Black level: the NumPy model, and its bit-exact agreement with the Verilog.

Rule 3: the only per-block correctness test is bit-exact agreement between the
model and the generated Verilog under Verilator via cocotb. The heavy lifting is
in ``tb_blacklevel.py``; this module builds the design, chooses the stimulus, and
covers the pure-Python behaviour that does not need a simulator.
"""
from __future__ import annotations

import numpy as np
import pytest

from conftest import raw_frame, requires_verilator, run_cocotb

from revela.blocks import blacklevel
from revela.stream import StreamSpec

OFFSET = blacklevel.blacklevel.params.declaration("offset")

WIDTH = 16
HEIGHT = 8
BIT_DEPTH = 12


# --------------------------------------------------------------------------- #
# The model
# --------------------------------------------------------------------------- #

def test_zero_offset_is_pass_through(rng):
    """Reset values must not change the image.

    An unconfigured pipeline should show a raised black level, not a wrong one:
    a black level block whose reset state mangles the picture makes bring-up on
    new hardware needlessly confusing.
    """
    frame = raw_frame(rng, WIDTH, HEIGHT, BIT_DEPTH)
    out = blacklevel.blacklevel.run(frame, {}, bayer_phase=0, bit_depth=BIT_DEPTH)
    np.testing.assert_array_equal(out, frame)


def test_saturates_at_zero_and_full_scale():
    """Below black clips to zero; a positive offset cannot exceed full scale."""
    top = (1 << BIT_DEPTH) - 1
    frame = np.array([[0, top], [10, top]], dtype=np.uint16)

    darker = OFFSET.values(np.full((2, 2), -64))
    assert blacklevel.blacklevel.run(frame, darker,
                                     bit_depth=BIT_DEPTH).tolist() == [
        [0, top - 64], [0, top - 64]]

    brighter = OFFSET.values(np.full((2, 2), 100))
    assert blacklevel.blacklevel.run(frame, brighter,
                                     bit_depth=BIT_DEPTH).tolist() == [
        [100, top], [110, top]]


def test_full_range_survives_a_signed_offset_register():
    """Pixels above the midpoint must not be read as negative.

    A datapath made signed by a signed coefficient register, but sized from the
    unsigned value range, is one bit short: the top half of the range reads
    negative and a following clip floors it to zero. That is not a hypothetical
    -- it is a real defect this test was written against, and it is invisible
    unless the stimulus crosses the midpoint.
    """
    top = (1 << BIT_DEPTH) - 1
    mid = 1 << (BIT_DEPTH - 1)
    frame = np.array([[mid - 1, mid], [top, top - 1]], dtype=np.uint16)
    out = blacklevel.blacklevel.run(frame, {}, bayer_phase=0, bit_depth=BIT_DEPTH)
    np.testing.assert_array_equal(out, frame)


@pytest.mark.parametrize("phase", [0, 1, 2, 3])
def test_offset_follows_the_colour_not_the_position(phase):
    """One bitstream, every sensor orientation.

    ``offset`` is indexed by CFA colour. Changing ``bayer_phase`` must move which
    POSITIONS receive a given colour's offset, without the host rewriting the
    coefficients -- that is what makes the phase a two-bit register worth having.
    """
    frame = np.zeros((2, 2), dtype=np.uint16)
    offsets = {"offset_0_0": 10, "offset_0_1": 20, "offset_1_0": 30,
               "offset_1_1": 40}
    out = blacklevel.blacklevel.run(frame, offsets, bayer_phase=phase, bit_depth=BIT_DEPTH)

    phase_row, phase_col = (phase >> 1) & 1, phase & 1
    for y in (0, 1):
        for x in (0, 1):
            colour = ((y ^ phase_row), (x ^ phase_col))
            assert out[y, x] == offsets[f"offset_{colour[0]}_{colour[1]}"]

    # Whatever the phase, all four offsets are used exactly once per CFA tile.
    assert sorted(out.ravel().tolist()) == [10, 20, 30, 40]


def test_odd_dimensions_are_rejected():
    """A CFA frame with an odd dimension has no consistent phase."""
    with pytest.raises(ValueError, match="odd dimension"):
        blacklevel.blacklevel.run(np.zeros((4, 5), dtype=np.uint16))


def test_offsets_from_sensor_negates_the_pedestal():
    """The datasheet states a positive pedestal; the register adds a negative."""
    sensor = {"black_level": {"pedestal": 64}}
    assert blacklevel.offsets_from_sensor(sensor) == {
        "offset_0_0": -64, "offset_0_1": -64,
        "offset_1_0": -64, "offset_1_1": -64,
    }

    per_colour = {"black_level": {"pedestal": 64,
                                  "per_colour": {"r": 64, "gr": 65, "gb": 66, "b": 67}}}
    assert blacklevel.offsets_from_sensor(per_colour) == {
        "offset_0_0": -64, "offset_0_1": -65,
        "offset_1_0": -66, "offset_1_1": -67,
    }


def test_rejects_a_stream_of_the_wrong_domain():
    """Black level is a Bayer-domain block; three channels means it is misplaced.

    The block does not check this itself. It DECLARES that its input carries
    Bayer, and one component per pixel follows from the domain -- so the same
    guard protects every block without any of them repeating it.
    """
    with pytest.raises(ValueError, match="consumes 'bayer'"):
        blacklevel.blacklevel.generate(
            StreamSpec(bit_depth=BIT_DEPTH, channels=3), 8, 8,
            module_name="revela_blacklevel")


# --------------------------------------------------------------------------- #
# Rule 3: bit-exact model versus generated Verilog
# --------------------------------------------------------------------------- #

@pytest.mark.verilog
@requires_verilator
def test_verilog_is_bit_exact_with_the_model(tmp_path, rng):
    """THE test for this block.

    Four trials in one elaboration, one per CFA phase, each with different
    offsets and a fresh frame, all under randomised backpressure on both sides.
    Running every phase against ONE build is the point: ``bayer_phase`` is a
    runtime register, so a single bitstream has to be correct for all four. If
    this needed four builds, the design would have failed the sensor rule.
    """
    spec = StreamSpec(bit_depth=BIT_DEPTH)
    generated = blacklevel.blacklevel.generate(spec, WIDTH, HEIGHT,
                                               module_name="revela_blacklevel")

    trials = []
    for phase in range(4):
        frame = raw_frame(rng, WIDTH, HEIGHT, BIT_DEPTH)
        offsets = OFFSET.values(rng.integers(-300, 301, (2, 2)))
        trials.append({
            "seed": int(rng.integers(0, 2**31)),
            "context": {"bayer_phase": phase},
            "values": offsets,
            "frame": frame.ravel().tolist(),
        })

    run_cocotb(
        tmp_path=tmp_path,
        verilog=generated.verilog,
        toplevel=generated.top,
        test_module="tb_block",
        case={
            "block": "blacklevel",
            "width": WIDTH,
            "height": HEIGHT,
            "bit_depth": BIT_DEPTH,
            "trials": trials,
        },
    )
