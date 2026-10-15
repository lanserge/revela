# Copyright 2026 Serge Rabyking
# SPDX-License-Identifier: Apache-2.0 WITH SHL-2.1
"""Crop: the slice, its reset, and bit-exact agreement."""
from __future__ import annotations

import numpy as np

from conftest import raw_frame, requires_verilator, run_cocotb

from revela.blocks import crop
from np2hw.blocks.stream import StreamSpec

WIDTH = 16
HEIGHT = 8
BIT_DEPTH = 12


def _reset():
    return {r.name: r.param.default for r in crop.crop.params.registers}


def test_reset_keeps_the_whole_frame(rng):
    """An unwritten crop shows the picture: the window covers any frame."""
    frame = raw_frame(rng, WIDTH, HEIGHT, BIT_DEPTH)
    np.testing.assert_array_equal(
        crop.crop.run(frame, _reset(), bit_depth=BIT_DEPTH), frame)


def test_a_window_past_the_frame_ends_at_its_edge(rng):
    frame = raw_frame(rng, WIDTH, HEIGHT, BIT_DEPTH)
    out = crop.crop.run(frame, {"y0": 5, "x0": 10, "h": 9, "w": 9},
                        bit_depth=BIT_DEPTH)
    np.testing.assert_array_equal(out, frame[5:, 10:])


@requires_verilator
def test_verilog_is_bit_exact_with_the_model(tmp_path, rng):
    """The whole frame, a window inside it, and one running off its corner,
    through one build."""
    generated = crop.crop.generate(StreamSpec(bit_depth=BIT_DEPTH),
                                   WIDTH, HEIGHT, module_name="revela_crop")
    windows = {
        "reset": _reset(),
        "inside": {"y0": 2, "x0": 3, "h": 4, "w": 7},
        "corner": {"y0": 5, "x0": 10, "h": 9, "w": 9},
    }
    trials = [{"seed": int(rng.integers(0, 2**31)), "label": label,
               "values": values,
               "frame": raw_frame(rng, WIDTH, HEIGHT,
                                  BIT_DEPTH).ravel().tolist()}
              for label, values in windows.items()]
    run_cocotb(tmp_path=tmp_path, verilog=generated.verilog,
               toplevel=generated.top, test_module="np2hw.testing.block",
               case={"block": "crop", "width": WIDTH, "height": HEIGHT,
                     "bit_depth": BIT_DEPTH, "trials": trials})
