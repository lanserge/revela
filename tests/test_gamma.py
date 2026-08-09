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
BIT_DEPTH = 12
FULL = (1 << BIT_DEPTH) - 1


def test_reset_is_exact_identity(rng):
    """The ramp default, including the top segment where LUTs lose a bit."""
    frame = raw_frame(rng, WIDTH, HEIGHT, BIT_DEPTH)
    out = gamma.gamma.run(frame, {}, bit_depth=BIT_DEPTH)
    np.testing.assert_array_equal(out, frame)


def test_knots_are_hit_exactly_and_lerp_is_truncating():
    """At a knot position the output IS the knot; between, the floor lerp."""
    table = KNOTS.values(np.array([0, 1000, 2000] + [0] * 30))
    frame = np.array([[128, 256], [129, 131]], dtype=np.uint16)
    out = gamma.gamma.run(frame, table, bit_depth=BIT_DEPTH)
    assert out[0, 0] == 1000 and out[0, 1] == 2000     # knots exactly
    #  between knots 1 and 2: base 1000 + (1000 * frac) >> 7
    assert out[1, 0] == 1000 + ((1000 * 1) >> 7)
    assert out[1, 1] == 1000 + ((1000 * 3) >> 7)


def test_a_falling_curve_is_legal():
    """Solarisation is a register write, and the signed step must survive."""
    table = KNOTS.values(np.array([(32 - i) * 128 for i in range(33)]))
    out = gamma.gamma.run(np.array([[0, 4095]], dtype=np.uint16), table,
                          bit_depth=BIT_DEPTH)
    assert out[0, 0] == FULL                                # 4096 clips to 4095
    assert out[0, 1] == 128 - ((128 * 127) >> 7)


def test_the_knot_count_is_the_declarations_not_a_constant():
    variant = gamma.gamma.configure({"knots": {"shape": [65], "bits": 13}})
    frame = np.array([[0, FULL], [2048, 2047]], dtype=np.uint16)
    out = variant(frame, variant.params.bind({}), variant.context_view({}),
                  BIT_DEPTH)
    np.testing.assert_array_equal(out, frame)
    assert len(variant.params.registers) == 65


def test_mismatched_knot_width_is_refused_with_the_override():
    with pytest.raises(ValueError, match='"bits": 11'):
        gamma.gamma.run(np.zeros((2, 2), np.uint16), bit_depth=10)


def test_knots_from_curve_identity_is_the_reset_ramp():
    """The host helper and default_ramp must agree on what identity means.

    The declaration NAMES the values -- curves only computes them -- so the
    round trip through ``declaration("knots").values(...)`` is part of what
    is under test here, alongside the arithmetic agreement.
    """
    computed = KNOTS.values(
        curves.knots_from_curve(lambda x: x, count=33, bit_depth=BIT_DEPTH))
    declared = {r.name: r.param.default for r in gamma.gamma.params.registers}
    assert computed == declared


def test_knots_from_table_resamples_through_np_interp():
    table = curves.knots_from_table([0.0, 0.5, 1.0], [0.0, 0.9, 1.0],
                                    count=33, bit_depth=BIT_DEPTH)
    assert table[0] == 0
    assert table[16] == round(0.9 * 4096)
    assert table[32] == 4096
    assert table[8] == round(0.45 * 4096)              # linear inside a span


def test_srgb_curve_fits_the_registers():
    values = curves.knots_from_curve(curves.srgb, count=33,
                                     bit_depth=BIT_DEPTH)
    assert values[0] == 0 and values[-1] == 4096
    assert all(0 <= v <= 8191 for v in values)          # 13-bit registers
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
                                                     BIT_DEPTH)),
        "random": KNOTS.values(rng.integers(0, 8192, 33)),
        "sawtooth": KNOTS.values(np.array([4096 * (i % 2)
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
