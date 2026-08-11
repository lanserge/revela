# Copyright 2026 Serge Rabyking
# SPDX-License-Identifier: Apache-2.0 WITH SHL-2.1
"""Colour correction matrix: the NumPy model, and its bit-exact Verilog.

Rule 3, same shape as the other block suites: the simulator half lives in
``tb_ccm.py``; this module builds the design, chooses stimulus that exercises
the signed and rounding paths deliberately, and covers the pure-Python
behaviour. The model speaks (h, w, 3); the wire word appears only where the
wire does -- the cocotb trials drive packed words built by this file's own
pack(), the deliberate second statement of the law that catches either end
packing differently.
"""
from __future__ import annotations

import numpy as np
import pytest

from conftest import requires_verilator, run_cocotb

from revela.blocks import ccm
from revela.stream import StreamSpec

# THE way a matrix becomes register writes: the declaration names its own
# values, so no test-side helper restates the shape or the leaf names.
MATRIX = ccm.ccm.params.declaration("m")

WIDTH = 16
HEIGHT = 8
BIT_DEPTH = 12
TOP = (1 << BIT_DEPTH) - 1
ONE = 1 << MATRIX.frac        # unity, read from the declaration


def pack(rgb: np.ndarray) -> np.ndarray:
    """(h, w, 3) channels -> (h, w) packed words, R in the low bits.

    A DELIBERATE second statement of the packing law, independent of both
    the model and StreamSpec.pack -- the tests' oracle for which bits are
    which channel. It is the only other statement; the testbench drives
    the packed words unchanged.
    """
    rgb = np.asarray(rgb, dtype=np.int64)
    return (rgb[..., 0]
            | (rgb[..., 1] << BIT_DEPTH)
            | (rgb[..., 2] << (2 * BIT_DEPTH))).astype(np.uint64)


def rgb_frame(rng, width=WIDTH, height=HEIGHT) -> np.ndarray:
    frame = rng.integers(0, TOP + 1, (height, width, 3)).astype(np.int64)
    # Rails in every channel: saturation and sign defects live at the ends.
    frame[0, 0], frame[0, 1] = (0, 0, 0), (TOP, TOP, TOP)
    frame[0, 2], frame[0, 3] = (TOP, 0, 0), (0, 0, TOP)
    return frame


# --------------------------------------------------------------------------- #
# The model
# --------------------------------------------------------------------------- #

def test_identity_reset_is_pass_through(rng):
    """Reset values must not change the image.

    An uncalibrated pipeline shows the sensor's colours, not wrong ones --
    the same bring-up argument as blacklevel's zero and whitebalance's
    unity, in matrix form. Pass-through THROUGH the packed word also proves
    unpack and repack agree on which bits are which channel.
    """
    frame = rgb_frame(rng)
    out = ccm.ccm.run(frame, {}, bit_depth=BIT_DEPTH)
    np.testing.assert_array_equal(out, frame)


def test_matches_the_dot_product_with_rounding(rng):
    """The model IS row-dot-input, +half, >>frac, clipped -- checked against
    an independently written integer matmul over every channel at once."""
    rgb = rgb_frame(rng)
    matrix = [[350, -70, -24], [-60, 380, -64], [10, -120, 366]]
    out = ccm.ccm.run(rgb, MATRIX.values(matrix), bit_depth=BIT_DEPTH)
    reference = np.clip((rgb @ np.array(matrix).T + 128) >> 8, 0, TOP)
    np.testing.assert_array_equal(out, reference)


def test_rounds_to_nearest_not_a_floor():
    """1 * 1.496 -> 1 but 1 * 1.5 -> 2: the half-LSB constant is real.

    This is the deliberate difference from whitebalance -- no loop sits
    behind this block to integrate a truncation bias away -- and it is
    exactly what a float reference would fail to pin down.
    """
    one_r = np.array([[[1, 0, 0]]])
    for coefficient, want in ((383, 1), (384, 2)):
        values = MATRIX.values(
            [[coefficient, 0, 0], [0, ONE, 0], [0, 0, ONE]])
        out = ccm.ccm.run(one_r, values, bit_depth=BIT_DEPTH)
        assert int(out[0, 0, 0]) == want, (coefficient, int(out[0, 0, 0]))


def test_saturates_at_zero_and_full_scale():
    """A negative dot product clips to 0; an overdriven one to full scale."""
    rgb = np.array([[[100, TOP, 0], [TOP, TOP, TOP]]])
    negative = MATRIX.values(
        [[ONE, -ONE, 0], [0, ONE, 0], [0, 0, ONE]])       # R' = R - G < 0
    out = ccm.ccm.run(rgb, negative, bit_depth=BIT_DEPTH)
    assert int(out[0, 0, 0]) == 0

    hot = MATRIX.values(
        [[2 * ONE, 0, 0], [0, 2 * ONE, 0], [0, 0, 2 * ONE]])
    out = ccm.ccm.run(rgb, hot, bit_depth=BIT_DEPTH)
    np.testing.assert_array_equal(out[0, 1], (TOP, TOP, TOP))


def test_row_is_the_output_channel():
    """A permutation matrix swaps R and B: rows select outputs, columns
    weigh inputs. Getting this transposed produces a plausible image with
    every memory colour wrong, so it is pinned as a semantic test."""
    rgb = np.array([[[1000, 2000, 3000]]])
    swap_rb = MATRIX.values(
        [[0, 0, ONE], [0, ONE, 0], [ONE, 0, 0]])
    out = ccm.ccm.run(rgb, swap_rb, bit_depth=BIT_DEPTH)
    np.testing.assert_array_equal(out, [[[3000, 2000, 1000]]])


def test_variant_identity_re_derives_with_frac():
    """A Q-format override moves unity, and the reset diagonal follows it.

    default_identity is a semantic invariant like default_unity: an
    uncalibrated VARIANT must also pass through, or "reset is safe" would
    quietly become "reset is x16" on any design that overrides frac.
    """
    variant = ccm.ccm.configure({"m": {"frac": 6}})
    declared = {r.name: r.param.default for r in variant.params.registers}
    assert declared["m_0_0"] == declared["m_1_1"] == declared["m_2_2"] == 64
    assert declared["m_0_1"] == declared["m_2_0"] == 0

    frame = np.array([[[7, 2049, TOP]]])
    out = variant.run(frame, {}, bit_depth=BIT_DEPTH)
    np.testing.assert_array_equal(out, frame)


def test_the_declaration_names_its_own_values():
    """Param.values: names from leaf_name, shape and range from the
    declaration -- there is no helper to hold a second copy of any of it."""
    values = MATRIX.values([[1, 2, 3], [4, 5, 6], [7, 8, 9]])
    assert values == {f"m_{i}_{j}": 3 * i + j + 1
                      for i in range(3) for j in range(3)}
    with pytest.raises(ValueError, match="declared"):
        MATRIX.values([[1, 2], [3, 4]])
    with pytest.raises(ValueError, match="outside"):
        MATRIX.values([[1 << 12, 0, 0], [0, 256, 0], [0, 0, 256]])


def test_rejects_a_stream_of_the_wrong_domain():
    """CCM is an RGB block; a single-channel stream means it is misplaced --
    upstream of demosaic, where it has nothing meaningful to multiply."""
    with pytest.raises(ValueError, match="consumes 'rgb'"):
        ccm.ccm.generate(StreamSpec(bit_depth=BIT_DEPTH), 8, 8,
                         module_name="revela_ccm")


# --------------------------------------------------------------------------- #
# Rule 3: bit-exact model versus generated Verilog
# --------------------------------------------------------------------------- #

@pytest.mark.verilog
@requires_verilator
def test_verilog_is_bit_exact_with_the_model(tmp_path, rng):
    """THE test for this block.

    Four matrices against ONE build -- identity, an R/B permutation, a
    calibration-shaped matrix with negative off-diagonals (the signed path),
    and a saturating one -- each on a fresh frame with all-rails corners,
    under randomised backpressure on both sides.
    """
    spec = StreamSpec(bit_depth=BIT_DEPTH, channels=3)
    generated = ccm.ccm.generate(spec, WIDTH, HEIGHT, module_name="revela_ccm")

    matrices = (
        ("identity", [[ONE, 0, 0], [0, ONE, 0], [0, 0, ONE]]),
        ("swap R and B", [[0, 0, ONE], [0, ONE, 0], [ONE, 0, 0]]),
        ("calibration-shaped, negative off-diagonals",
         [[350, -70, -24], [-60, 380, -64], [10, -120, 366]]),
        ("saturating", [[2 * ONE, ONE, 0], [-ONE, 2 * ONE, ONE // 2],
                        [0, -2 * ONE, 2 * ONE]]),
    )
    trials = []
    for label, matrix in matrices:
        trials.append({
            "seed": int(rng.integers(0, 2**31)),
            "label": label,
            "values": MATRIX.values(matrix),
            "frame": pack(rgb_frame(rng)).ravel().tolist(),
        })

    run_cocotb(
        tmp_path=tmp_path,
        verilog=generated.verilog,
        toplevel=generated.top,
        test_module="tb_block",
        case={
            "block": "ccm",
            "width": WIDTH,
            "height": HEIGHT,
            "bit_depth": BIT_DEPTH,
            "channels": 3,
            "trials": trials,
        },
    )
