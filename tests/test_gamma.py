# Copyright 2026 Serge Rabyking
# SPDX-License-Identifier: Apache-2.0 WITH SHL-2.1
"""Gamma: the PWL model, the host curve helpers, and bit-exact agreement."""
from __future__ import annotations

import numpy as np
import pytest

from conftest import raw_frame, requires_verilator, run_cocotb

from revela.blocks import gamma
from revela.host import curves
from revela.stream import StreamSpec

KNOTS = gamma.gamma.params.declaration("knots")

WIDTH = 16
HEIGHT = 8
BIT_DEPTH = 12                      # what ARRIVES: linear, from the chain
OUT_BITS = KNOTS.bits - 1           # what LEAVES: display values, 8 by
OUT_FULL = (1 << OUT_BITS) - 1      # default, and the knots own the number
FULL = (1 << BIT_DEPTH) - 1
SHIFT = BIT_DEPTH - OUT_BITS


def test_reset_scales_the_picture_and_nothing_else(rng):
    """The ramp default: full scale in lands on full scale out, exactly.

    Gamma is where linear light becomes display values, so identity can no
    longer mean pass-through -- the word narrows here. What survives is the
    bring-up promise: an unconfigured pipeline shows the picture, scaled and
    otherwise untouched. When the depths differ by a power of two that is a
    pure shift, so it can be checked exactly rather than approximately.
    """
    frame = raw_frame(rng, WIDTH, HEIGHT, BIT_DEPTH)
    out = gamma.gamma.run(frame, {}, bit_depth=BIT_DEPTH)
    np.testing.assert_array_equal(out, frame >> SHIFT)
    assert out.max() <= OUT_FULL


def test_knots_are_hit_exactly_and_lerp_is_truncating():
    """At a knot position the output IS the knot; between, the floor lerp.

    Knot values are in OUTPUT units now, so they must fit the output.
    """
    table = KNOTS.values(np.array([0, 100, 200] + [0] * 30))
    frame = np.array([[128, 256], [129, 131]], dtype=np.uint16)
    out = gamma.gamma.run(frame, table, bit_depth=BIT_DEPTH)
    assert out[0, 0] == 100 and out[0, 1] == 200       # knots exactly
    #  between knots 1 and 2: base 100 + (100 * frac) >> 7
    assert out[1, 0] == 100 + ((100 * 1) >> 7)
    assert out[1, 1] == 100 + ((100 * 3) >> 7)


def test_a_falling_curve_is_legal():
    """Solarisation is a register write, and the signed step must survive."""
    table = KNOTS.values(np.array([(32 - i) * 8 for i in range(33)]))
    out = gamma.gamma.run(np.array([[0, FULL]], dtype=np.uint16), table,
                          bit_depth=BIT_DEPTH)
    assert out[0, 0] == OUT_FULL                        # 256 clips to 255
    # The lerp shift FLOORS, so a falling segment is not the mirror of a
    # rising one: -1016 >> 7 is -8, not -7. Written as the model computes
    # it, because that asymmetry is the thing under test.
    assert out[0, 1] == 8 + ((-8 * 127) >> 7)


def test_the_knot_count_is_the_declarations_not_a_constant():
    """More knots, and a design that keeps the full depth to the end.

    bits=13 asks for 12-bit output, which at a 12-bit input IS a
    pass-through -- so the same declaration that sets the output depth
    also recovers the old behaviour for a design that wants it.
    """
    variant = gamma.gamma.configure({"knots": {"shape": [65], "bits": 13}})
    frame = np.array([[0, FULL], [2048, 2047]], dtype=np.uint16)
    out = variant(frame, variant.params.bind({}), variant.context_view({}),
                  BIT_DEPTH)
    np.testing.assert_array_equal(out, frame)
    assert len(variant.params.registers) == 65


def test_a_curve_cannot_invent_precision():
    """Asking for more out than came in is refused, not silently allowed.

    A curve shapes the levels it was given. The default 8-bit output is
    fine from any sensible input; 12-bit output from an 8-bit input is not,
    and saying so beats emitting a datapath whose low bits are invented.
    """
    variant = gamma.gamma.configure({"knots": {"bits": 13}})
    with pytest.raises(ValueError, match="cannot invent precision"):
        variant(np.zeros((2, 2), np.uint16), variant.params.bind({}),
                variant.context_view({}), 8)


def test_knots_from_curve_identity_is_the_reset_ramp():
    """The host helper and default_ramp must agree on what identity means.

    The declaration NAMES the values -- curves only computes them -- so the
    round trip through ``declaration("knots").values(...)`` is part of what
    is under test here, alongside the arithmetic agreement.
    """
    computed = KNOTS.values(
        curves.knots_from_curve(lambda x: x, count=33, out_bits=OUT_BITS))
    declared = {r.name: r.param.default for r in gamma.gamma.params.registers}
    assert computed == declared


def test_knots_from_table_resamples_through_np_interp():
    top = 1 << OUT_BITS
    table = curves.knots_from_table([0.0, 0.5, 1.0], [0.0, 0.9, 1.0],
                                    count=33, out_bits=OUT_BITS)
    assert table[0] == 0
    assert table[16] == round(0.9 * top)
    assert table[32] == top
    assert table[8] == round(0.45 * top)               # linear inside a span


def test_srgb_curve_fits_the_registers():
    values = curves.knots_from_curve(curves.srgb, count=33,
                                     out_bits=OUT_BITS)
    assert values[0] == 0 and values[-1] == (1 << OUT_BITS)
    assert all(0 <= v <= (1 << KNOTS.bits) - 1 for v in values)
    assert all(b >= a for a, b in zip(values, values[1:]))
    KNOTS.values(values)                                     # and they bind


# --------------------------------------------------------------------------- #
# Rule 3: every curve against ONE elaboration
# --------------------------------------------------------------------------- #

@pytest.mark.verilog
@requires_verilator
def test_verilog_is_bit_exact_with_the_model(tmp_path, rng):
    """Identity, sRGB, random and sawtooth tables through one build.

    A tone curve is runtime state; a single bitstream must be right for all
    of them, including falling segments (signed steps) and the corners the
    stimulus pins (0, full scale, the midpoint pair).
    """
    generated = gamma.gamma.generate(StreamSpec(bit_depth=BIT_DEPTH),
                                     WIDTH, HEIGHT,
                                     module_name="revela_gamma")
    tables = {
        # Identity is the declared reset, read from the declaration rather
        # than re-derived here -- default_ramp owns what identity means.
        "identity": {r.name: r.param.default
                     for r in gamma.gamma.params.registers},
        "srgb": KNOTS.values(curves.knots_from_curve(curves.srgb, 33,
                                                     OUT_BITS)),
        "random": KNOTS.values(rng.integers(0, 1 << KNOTS.bits, 33)),
        "sawtooth": KNOTS.values(np.array([(1 << OUT_BITS) * (i % 2)
                                           for i in range(33)])),
    }
    trials = [{"seed": int(rng.integers(0, 2**31)), "label": label,
               "values": table,
               "frame": raw_frame(rng, WIDTH, HEIGHT, BIT_DEPTH).ravel().tolist()}
              for label, table in tables.items()]
    run_cocotb(tmp_path=tmp_path, verilog=generated.verilog,
               toplevel=generated.top, test_module="tb_block",
               case={"block": "gamma", "width": WIDTH, "height": HEIGHT,
                     "bit_depth": BIT_DEPTH, "trials": trials})
