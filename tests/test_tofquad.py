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
from revela.blocks.tofquad import tofquad
from revela.stream import StreamSpec

WIDTH = 16
HEIGHT = 8
BIT_DEPTH = 16                  # the link carries each phase as a Y16 sample
OFFSET = 1 << 15                # signed correlations ride unsigned under this
SPEC = StreamSpec(bit_depth=BIT_DEPTH, channels=8)    # 4 samples + 4 tags
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


def _window(planes, pos, order=ASCENDING):
    """A frame history's word when the live frame sits at loop position
    ``pos``: frame n-k sits at position (pos - k) mod 4 and was captured at
    phase ``order[position]``. Channels 0..3 are the samples, riding under
    the link's offset; channels 4..7 are the phase tags."""
    phase = [order[(pos - k) % 4] for k in range(4)]
    samples = [planes[ph] + OFFSET for ph in phase]
    tags = [np.full_like(planes[0], ph) for ph in phase]
    return np.stack(samples + tags, axis=-1)


# --------------------------------------------------------------------------- #
# The model
# --------------------------------------------------------------------------- #

def test_every_phase_alignment_gives_the_same_measurement(rng):
    """The same four phases, whichever arrived last, are the same measurement.

    This is what makes the output valid on every frame rather than one in four:
    the tags route the frames onto the quadrature so that the result
    depends on WHICH phases are present, never on the order they came in. A
    routing table off by one would pass a single-alignment test and fail here.
    """
    planes = _phases(rng)
    results = [tofquad.run(_window(planes, pos), {}, bit_depth=BIT_DEPTH)
               for pos in range(4)]
    for pos in range(1, 4):
        np.testing.assert_array_equal(results[pos], results[0],
                                      err_msg=f"loop position {pos}")


def test_the_phase_order_is_the_tables_business(rng):
    """A sensor stepping its phases 0, 2, 1, 3 is a different tag table, not a
    different block: the same four phases give the same measurement, at every
    alignment. A block that assumed ascending tags would fail here."""
    planes = _phases(rng)
    want = tofquad.run(_window(planes, 0), {}, bit_depth=BIT_DEPTH)
    for order in ((0, 2, 1, 3), (3, 1, 0, 2)):
        for pos in range(4):
            np.testing.assert_array_equal(
                tofquad.run(_window(planes, pos, order), {}, bit_depth=BIT_DEPTH),
                want, err_msg=f"order {order}, position {pos}")


def test_a_pedestal_on_every_frame_changes_nothing(rng):
    """Ambient light adds the same amount to all four phases; it must vanish.

    Every quantity the block computes is a difference of two frames, so a
    common offset cancels EXACTLY -- which is also why the link's own offset
    needs no handling here.
    """
    planes = _phases(rng)
    planes[:, 0, 1:3] = 0                           # keep the extremes in range
    base = tofquad.run(_window(planes, 1), {}, bit_depth=BIT_DEPTH)
    lifted = tofquad.run(_window(planes + 700, 1), {}, bit_depth=BIT_DEPTH)
    np.testing.assert_array_equal(lifted, base)


def test_opposite_light_is_exactly_half_a_turn_away(rng):
    """Swapping phases 0<->2 and 1<->3 negates I and Q: the same vector turned
    exactly half a turn. The CORDIC folds the left half-plane by negating and
    starting half a turn on, so the two follow the SAME path: amplitude
    identical, angle exactly ``1 << 15`` apart. Exact, so it pins the fold."""
    planes = _phases(rng)
    planes[:, 0, :] = rng.integers(-2400, 2400, (4, WIDTH))    # no zero vectors
    turned = planes[[2, 3, 0, 1]]
    a = tofquad.run(_window(planes, 0), {}, bit_depth=BIT_DEPTH)
    b = tofquad.run(_window(turned, 0), {}, bit_depth=BIT_DEPTH)
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
        a = tofquad.run(_window(dim, 2), {}, bit_depth=BIT_DEPTH)
        b = tofquad.run(_window(bright, 2), {}, bit_depth=BIT_DEPTH)
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

    Trials in one elaboration -- every loop position, and a scrambled phase
    order -- each with fresh phases, under randomised backpressure on both
    sides. One build for all of them is the point: the tags change every
    frame, so a single bitstream has to be right whatever they say.
    """
    generated = tofquad.generate(SPEC, WIDTH, HEIGHT, module_name="revela_tofquad")

    trials = []
    for pos, order in [(p, ASCENDING) for p in range(4)] + [(1, (0, 2, 1, 3))]:
        window = _window(_phases(rng), pos, order)
        words = [[SPEC.pack(int(c) for c in px) for px in row] for row in window]
        trials.append({
            "seed": int(rng.integers(0, 2**31)),
            "label": f"position {pos}, order {order}",
            "frame": [w for row in words for w in row],
        })

    run_cocotb(
        tmp_path=tmp_path,
        verilog=generated.verilog,
        toplevel=generated.top,
        test_module="tb_block",
        case={
            "block": "tofquad",
            "width": WIDTH,
            "height": HEIGHT,
            "bit_depth": BIT_DEPTH,
            "in_channels": 8,
            "trials": trials,
        },
    )
