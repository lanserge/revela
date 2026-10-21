# Copyright 2026 Serge Rabyking
# SPDX-License-Identifier: Apache-2.0 WITH SHL-2.1
"""Time-of-flight quadrature: the NumPy model, and its bit-exact Verilog.

Rule 3: the only per-block correctness test is bit-exact agreement between the
model and the generated Verilog under Verilator via cocotb. The model tests
here pin the two things that test cannot: that every phase alignment yields the
same measurement, and that the angle is the one the light implies.
"""
from __future__ import annotations

import math

import numpy as np
import pytest

from conftest import requires_verilator, run_cocotb

from revela.blocks import tofquad as module
from revela.blocks.pipe import VOCABULARY
from revela.blocks.tofquad import tofquad
from np2hw.blocks.stream import StreamSpec

WIDTH = 16
HEIGHT = 8
BIT_DEPTH = 16                  # the datapath's word for each phase sample
OFFSET = 1 << 15                # the extremes of a 16-bit signed sample
SPEC = StreamSpec(bit_depth=BIT_DEPTH, channels=1)
ASCENDING = (0, 1, 2, 3)


def _phases(rng, height=HEIGHT, width=WIDTH):
    """Four phase planes of signed correlations, the awkward values included.

    Real samples span about +-2300; the extremes of the 16-bit field are placed
    too, because a difference of two of them is where a narrow datapath wraps.
    """
    planes = rng.integers(-2400, 2400, (4, height, width))
    planes[:, 0, 0] = 0                                  # a zero vector
    planes[:, 0, 1] = (-OFFSET, OFFSET - 1, OFFSET - 1, -OFFSET)
    planes[:, 0, 2] = (OFFSET - 1, -OFFSET, -OFFSET, OFFSET - 1)
    return planes


def _word(plane, headroom=0):
    """Signed samples as the stream's words: two's complement below
    `headroom` zero bits -- the sample's bits widened to the datapath, the
    bits above its sign clear."""
    return plane.astype(np.int64) & ((1 << (BIT_DEPTH - headroom)) - 1)


def _frames(planes, start, order=ASCENDING, count=4, headroom=0):
    """The sensor's frames, the first at loop place ``start``: frame n sits
    at place (start + n) mod 4 and was captured at phase ``order[place]``."""
    return np.stack([_word(planes[order[(start + n) % 4]], headroom)
                     for n in range(count)])


def _measure(planes, start, order=ASCENDING):
    """The one measurement four frames make: the live one's place is the
    last frame's."""
    values = {"pos": (start + 3) % 4,
              **{f"tag_{i}": order[i] for i in range(4)}}
    return tofquad.run(_frames(planes, start, order), values,
                       bit_depth=BIT_DEPTH)[0]


# --------------------------------------------------------------------------- #
# The model
# --------------------------------------------------------------------------- #

def test_every_phase_alignment_gives_the_same_measurement(rng):
    """The same four phases, whichever arrived last, are the same measurement.

    This is what makes the output valid on every frame rather than one in four:
    the places route the frames onto the quadrature so that the result
    depends on WHICH phases are present, never on the order they came in. A
    routing off by one would pass a single-alignment test and fail here.
    """
    planes = _phases(rng)
    results = [_measure(planes, pos) for pos in range(4)]
    for pos in range(1, 4):
        np.testing.assert_array_equal(results[pos], results[0],
                                      err_msg=f"loop position {pos}")


def test_the_phase_order_is_the_tables_business(rng):
    """A sensor stepping its phases 0, 2, 1, 3 is a different tag table, not a
    different block: the same four phases give the same measurement, at every
    alignment. A block that assumed ascending tags would fail here."""
    planes = _phases(rng)
    want = _measure(planes, 0)
    for order in ((0, 2, 1, 3), (3, 1, 0, 2)):
        for pos in range(4):
            np.testing.assert_array_equal(
                _measure(planes, pos, order), want,
                err_msg=f"order {order}, position {pos}")


def test_a_pedestal_on_every_frame_changes_nothing(rng):
    """Ambient light adds the same amount to all four phases; it must vanish.

    Every quantity the block computes is a difference of two frames, so a
    common offset cancels EXACTLY.
    """
    planes = _phases(rng)
    planes[:, 0, 1:3] = 0                           # keep the extremes in range
    base = _measure(planes, 1)
    lifted = _measure(planes + 700, 1)
    np.testing.assert_array_equal(lifted, base)


def test_a_sample_below_headroom_measures_as_a_whole_word_does(rng):
    """Eleven bits in a twelve-bit word, widened to sixteen, leave the bit
    above the sign clear: read with that headroom, each sample is the same
    signed value, and the measurement the same, as the word read whole."""
    planes = rng.integers(-1024, 1024, (4, HEIGHT, WIDTH)) << 4
    values = {"pos": 3}
    whole = tofquad.run(_frames(planes, 0), values, bit_depth=BIT_DEPTH)
    short = tofquad.run(_frames(planes, 0, headroom=1),
                        dict(values, headroom=1), bit_depth=BIT_DEPTH)
    np.testing.assert_array_equal(short, whole)
    # and read whole, a word whose sign is below the top is another value
    wrong = tofquad.run(_frames(planes, 0, headroom=1), values,
                        bit_depth=BIT_DEPTH)
    assert (wrong != whole).any()


def test_opposite_light_is_exactly_half_a_turn_away(rng):
    """Swapping phases 0<->2 and 1<->3 negates I and Q: the same vector turned
    exactly half a turn. The CORDIC folds the left half-plane by negating and
    starting half a turn on, so the two follow the SAME path: amplitude
    identical, angle exactly ``1 << 15`` apart. Exact, so it pins the fold."""
    planes = _phases(rng)
    planes[:, 0, :] = rng.integers(-2400, 2400, (4, WIDTH))    # no zero vectors
    turned = planes[[2, 3, 0, 1]]
    a = _measure(planes, 0)
    b = _measure(turned, 0)
    i = planes[0] - planes[2]
    live = i != 0            # on the Q axis the fold does not apply either way
    np.testing.assert_array_equal(b[..., 1][live], a[..., 1][live])
    half = 1 << (module.PHASE_BITS - 1)
    np.testing.assert_array_equal(
        (b[..., 0][live].astype(np.int64) - a[..., 0][live]) % (2 * half), half)


def test_a_dim_pixel_measures_the_same_angle_as_a_bright_one(rng):
    """What the pre-scale is FOR, stated exactly.

    A pixel that returned less light measures the same phase as a bright one
    pointing the same way. The pre-scale normalises both to the same operands
    before the CORDIC, so their angles are IDENTICAL -- not close. Without it,
    each round's shift starves the dim operand and the two angles part, worst
    exactly where the sensor is already short of light.
    """
    dim = rng.integers(-24, 24, (4, HEIGHT, WIDTH))
    dim[:, 0, 0] = (3, -5, -3, 5)                   # no zero vectors
    dim[0] = np.where(dim[0] == dim[2], dim[0] + 1, dim[0])
    for k in (4, 8, 10):
        bright = dim << k
        a = _measure(dim, 2)
        b = _measure(bright, 2)
        np.testing.assert_array_equal(b[..., 0], a[..., 0],
                                      err_msg=f"scaled by 2**{k}")


def test_the_atan_table_is_the_formula():
    """The baked table is what its comment says it is.

    The block holds integer literals so the model contains nothing
    transcendental; this is where the derivation is checked, in a test,
    where floating point is allowed.
    """
    turn = 1 << module.PHASE_BITS
    assert module.ATAN == [round(math.atan2(1, 2 ** i) / (2 * math.pi) * turn)
                           for i in range(module.ITERATIONS)]


def test_the_barrel_shifter_covers_the_pre_scale():
    """The log shifter's stages must reach every shift the pre-scale asks."""
    assert (1 << module.SHIFT_STAGES) - 1 >= module.NORM_BITS


# --------------------------------------------------------------------------- #
# Rule 3: bit-exact model versus generated Verilog
# --------------------------------------------------------------------------- #

@pytest.mark.verilog
@requires_verilator
def test_verilog_is_bit_exact_with_the_model(tmp_path, rng):
    """THE test for this block.

    One run of frames through one elaboration, under randomised backpressure
    on both sides: the place bound to the frame count each frame's header
    carries, so the alignment moves every frame -- every one of the four
    met twice -- in a scrambled phase order, and the first three frames,
    which the history is filling, put out nothing.
    """
    order = (0, 2, 1, 3)
    # the sensor's samples one bit short of the word, as a 12-bit link word
    # of eleven bits widened to sixteen: headroom bound to the sensor's
    bound = tofquad.bind({"pos": "frame_seq & 3", "headroom": "1"},
                         VOCABULARY)
    generated = bound.generate(SPEC, WIDTH, HEIGHT,
                               module_name="revela_tofquad")
    planes = _phases(rng)
    first = 37                                        # any count: it wraps
    trials = [{
        "seed": int(rng.integers(0, 2**31)),
        "label": f"frame_seq {first + n}",
        "context": {"frame_seq": first + n},
        "values": {f"tag_{i}": order[i] for i in range(4)},
        "frame": _word(planes[order[(first + n) & 3]] >> 1,
                       headroom=1).ravel().tolist(),
    } for n in range(11)]

    run_cocotb(
        tmp_path=tmp_path,
        verilog=generated.verilog,
        toplevel=generated.top,
        test_module="np2hw.testing.block",
        case={
            "block": "tofquad",
            "width": WIDTH,
            "height": HEIGHT,
            "bit_depth": BIT_DEPTH,
            "bind": {"pos": "frame_seq & 3", "headroom": "1"},
            "vocabulary": "revela.blocks.pipe:VOCABULARY",
            "trials": trials,
        },
    )
