# Copyright 2026 Serge Rabyking
# SPDX-License-Identifier: Apache-2.0 WITH SHL-2.1
"""Hamilton-Adams demosaic: two models, two bit-exact Verilog stages.

Rule 3 per stage, plus the property the split must not break: the two
stages CHAINED equal one whole-image oracle written as a single 7x7-ish
window walk with none of the models' slicing. The adaptive decision gets
its own pins -- direction choice, the tie, and floor/clip on the corrected
estimates.
"""
from __future__ import annotations

import numpy as np
import pytest

from conftest import requires_verilator, run_cocotb

from revela.blocks.demosaic import hamilton_adams as ha
from revela.stream import StreamSpec

WIDTH = 16
HEIGHT = 12
BIT_DEPTH = 12
TOP = (1 << BIT_DEPTH) - 1


def green_oracle(frame, phase):
    """(h, w) reconstructed green, per pixel, independent window walk."""
    pr, pc = (phase >> 1) & 1, phase & 1
    height, width = frame.shape
    x = np.pad(frame.astype(np.int64), 2, mode="edge")
    out = np.zeros((height, width), dtype=np.int64)
    for y in range(height):
        for c in range(width):
            w = x[y:y + 5, c:c + 5]              # centre at (2, 2)
            green_site = ((y & 1) == pr) != ((c & 1) == pc)
            if green_site:
                out[y, c] = w[2, 2]
                continue
            lh = 2 * w[2, 2] - w[2, 0] - w[2, 4]
            lv = 2 * w[2, 2] - w[0, 2] - w[4, 2]
            dh = abs(w[2, 1] - w[2, 3]) + abs(lh)
            dv = abs(w[1, 2] - w[3, 2]) + abs(lv)
            if dh < dv:
                est = (w[2, 1] + w[2, 3]) // 2 + lh // 4
            elif dv < dh:
                est = (w[1, 2] + w[3, 2]) // 2 + lv // 4
            else:
                est = ((w[2, 1] + w[2, 3] + w[1, 2] + w[3, 2]) // 4
                       + (lh + lv) // 8)
            out[y, c] = min(max(est, 0), TOP)
    return out


def rb_oracle(raw, green, phase):
    """(h, w, 3) from the colour-difference rule, independent walk."""
    pr, pc = (phase >> 1) & 1, phase & 1
    height, width = raw.shape
    rp = np.pad(raw.astype(np.int64), 1, mode="edge")
    gp = np.pad(green.astype(np.int64), 1, mode="edge")
    out = np.zeros((height, width, 3), dtype=np.int64)
    for y in range(height):
        for c in range(width):
            rw, gw = rp[y:y + 3, c:c + 3], gp[y:y + 3, c:c + 3]
            dd = rw - gw
            gc = int(gw[1, 1])
            cent = int(rw[1, 1])
            horiz = min(max(gc + (dd[1, 0] + dd[1, 2]) // 2, 0), TOP)
            vert = min(max(gc + (dd[0, 1] + dd[2, 1]) // 2, 0), TOP)
            diag = min(max(
                gc + (dd[0, 0] + dd[0, 2] + dd[2, 0] + dd[2, 2]) // 4, 0), TOP)
            r_row, r_col = (y & 1) == pr, (c & 1) == pc
            if r_row and r_col:
                out[y, c] = (cent, gc, diag)
            elif r_row:
                out[y, c] = (horiz, gc, vert)
            elif r_col:
                out[y, c] = (vert, gc, horiz)
            else:
                out[y, c] = (diag, gc, cent)
    return out


def raw_frame(rng, width=WIDTH, height=HEIGHT):
    frame = rng.integers(0, TOP + 1, (height, width)).astype(np.uint16)
    frame[:, 4:7] = TOP                          # a hard vertical edge
    frame[3, :] = 0                              # and a horizontal one
    return frame


def pack2(word):
    return (word[..., 0].astype(np.int64)
            | (word[..., 1].astype(np.int64) << BIT_DEPTH)).astype(np.uint64)


# --------------------------------------------------------------------------- #
# The models
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize("phase", range(4))
def test_green_matches_the_oracle(rng, phase):
    frame = raw_frame(rng)
    out = ha.ha_green.run(frame, {}, bit_depth=BIT_DEPTH, bayer_phase=phase)
    np.testing.assert_array_equal(out[..., 0], frame)     # raw rides along
    np.testing.assert_array_equal(out[..., 1], green_oracle(frame, phase))


@pytest.mark.parametrize("phase", range(4))
def test_rb_matches_the_oracle(rng, phase):
    frame = raw_frame(rng)
    word = ha.ha_green.run(frame, {}, bit_depth=BIT_DEPTH, bayer_phase=phase)
    out = ha.ha_rb.run(word, {}, bit_depth=BIT_DEPTH, bayer_phase=phase)
    np.testing.assert_array_equal(
        out, rb_oracle(word[..., 0], word[..., 1], phase))


@pytest.mark.parametrize("phase", range(4))
def test_flat_field_stays_flat(rng, phase):
    for level in (0, 7, TOP):
        frame = np.full((HEIGHT, WIDTH), level, dtype=np.uint16)
        word = ha.ha_green.run(frame, {}, bit_depth=BIT_DEPTH,
                               bayer_phase=phase)
        out = ha.ha_rb.run(word, {}, bit_depth=BIT_DEPTH, bayer_phase=phase)
        np.testing.assert_array_equal(out, np.full((HEIGHT, WIDTH, 3), level))


def test_the_decision_follows_the_edge():
    """A vertical edge: dh is large, dv is zero, so green interpolates
    VERTICALLY through the edge and reproduces the column exactly."""
    frame = np.zeros((6, 8), dtype=np.uint16)
    frame[:, 4:] = 1000                          # vertical step at x=4
    out = ha.ha_green.run(frame, {}, bit_depth=BIT_DEPTH, bayer_phase=0)
    green = out[..., 1]
    # (2, 4) is an R site (phase 0): along the edge G is 1000 above/below.
    assert green[2, 4] == 1000
    # And on the dark side at the B site (1, 3): 0 above/below.
    assert green[1, 3] == 0


def test_the_tie_averages_both_axes():
    """dh == dv must take the four-neighbour branch, not either direction.

    A single hot green pixel north of an R site makes dh = dv = 0 except
    through the shared Laplacian -- constructed so both gradients agree.
    """
    frame = np.zeros((6, 8), dtype=np.uint16)
    frame[1, 2], frame[3, 2] = 100, 100          # gn = gs
    frame[2, 1], frame[2, 3] = 100, 100          # gw = ge
    out = ha.ha_green.run(frame, {}, bit_depth=BIT_DEPTH, bayer_phase=0)
    assert out[2, 2, 1] == 100                   # mean of four equals 100


def test_correction_overshoot_clips():
    """The Laplacian correction can push the estimate past the rails."""
    frame = np.zeros((6, 8), dtype=np.uint16)
    frame[2, 1], frame[2, 3] = TOP, TOP          # gw = ge = TOP
    frame[2, 2] = TOP                            # centre bright, X at +-2 dark:
    #   lh = 2*TOP, dh = 0 + 2*TOP
    frame[3, 2] = TOP                            # gn=0, gs=TOP -> dv = 3*TOP
    out = ha.ha_green.run(frame, {}, bit_depth=BIT_DEPTH, bayer_phase=0)
    # dh < dv: gh = TOP + (2*TOP)//4 overshoots and must clip.
    assert out[2, 2, 1] == TOP


def test_domains_are_declared():
    with pytest.raises(ValueError, match="consumes 'bayer'"):
        ha.ha_green.generate(StreamSpec(bit_depth=BIT_DEPTH, channels=3),
                             8, 8, module_name="x")
    with pytest.raises(ValueError, match="consumes 'bayer\\+g'"):
        ha.ha_rb.generate(StreamSpec(bit_depth=BIT_DEPTH, channels=1),
                          8, 8, module_name="x")


# --------------------------------------------------------------------------- #
# Rule 3: bit-exact model versus generated Verilog, per stage
# --------------------------------------------------------------------------- #

@pytest.mark.verilog
@requires_verilator
def test_green_verilog_is_bit_exact(tmp_path, rng):
    spec = StreamSpec(bit_depth=BIT_DEPTH)
    generated = ha.ha_green.generate(spec, WIDTH, HEIGHT,
                                     module_name="revela_ha_green")
    trials = [{"seed": int(rng.integers(0, 2**31)),
               "context": {"bayer_phase": phase}, "values": {},
               "frame": raw_frame(rng).ravel().tolist()}
              for phase in range(4)]
    run_cocotb(tmp_path=tmp_path, verilog=generated.verilog,
               toplevel=generated.top, test_module="tb_block",
               case={"block": "ha_green", "width": WIDTH, "height": HEIGHT,
                     "bit_depth": BIT_DEPTH, "channels": 2, "trials": trials})


@pytest.mark.verilog
@requires_verilator
def test_rb_verilog_is_bit_exact(tmp_path, rng):
    spec = StreamSpec(bit_depth=BIT_DEPTH, channels=2)
    generated = ha.ha_rb.generate(spec, WIDTH, HEIGHT,
                                  module_name="revela_ha_rb")
    trials = []
    for phase in range(4):
        word = ha.ha_green.run(raw_frame(rng), {}, bit_depth=BIT_DEPTH,
                               bayer_phase=phase)
        trials.append({"seed": int(rng.integers(0, 2**31)),
                       "context": {"bayer_phase": phase}, "values": {},
                       "frame": pack2(word).ravel().tolist()})
    run_cocotb(tmp_path=tmp_path, verilog=generated.verilog,
               toplevel=generated.top, test_module="tb_block",
               case={"block": "ha_rb", "width": WIDTH, "height": HEIGHT,
                     "bit_depth": BIT_DEPTH, "channels": 3, "trials": trials})
