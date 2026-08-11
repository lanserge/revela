# Copyright 2026 Serge Rabyking
# SPDX-License-Identifier: Apache-2.0 WITH SHL-2.1
"""THE cocotb testbench for stream blocks -- one testbench, every block.

Not collected by pytest -- each block's ``tests/test_<block>.py`` builds the
design, chooses stimulus, and invokes this through the cocotb runner.

There used to be one of these per block, and they were four copies of one
file: the same case loading, the same reset, the same trial loop, the same
comparison. Everything that differed is a fact the block already DECLARES --
which registers exist and how wide they are (``params``), how pipeline
context splits into port bits (``context``), what output to expect
(``Block.run``) -- so this file reads the declarations and the per-block
testbenches no longer exist, for the same reason the per-block ``model()``
adapters no longer exist.

The case names the block; the block comes from the registry; overrides
reconfigure it, so a variant's RTL is checked against the variant's own
model -- one configured declaration on both sides.

What travels on ``data`` is the PACKED word (the model's own input format),
whatever the channel count. Framing is asserted by ``np2hw.testing.
check_framing`` -- the contract's owner states the law; this file does not
restate it.
"""
from __future__ import annotations

import json
import os
import random
from pathlib import Path

import numpy as np

import cocotb
from cocotb.clock import Clock
from cocotb.triggers import RisingEdge

from np2hw.testing import check_framing, frame_to_beats, reset_stream, run_frame
from revela.blocks import resolve
from revela.stream import StreamSpec


def _load_case() -> dict:
    return json.loads(Path(os.environ["REVELA_CASE"]).read_text())


def _diagnose(block, case, trial, i, got, want, frame):
    """Name the failing pixel in the block's own terms, from the declarations."""
    width = case["width"]
    bit_depth = case["bit_depth"]
    row, col = divmod(i, width)
    parts = [f"pixel {i} (row {row}, col {col}) -- DUT {got}, model {want}, "
             f"input {int(frame[row, col])}"]
    channels = case.get("channels", 1)
    if channels > 1:
        top = (1 << bit_depth) - 1
        unpack = lambda w: [(w >> (k * bit_depth)) & top for k in range(channels)]
        parts.append(f"per channel DUT {unpack(got)}, model {unpack(want)}")
    context = trial.get("context", {})
    if "bayer_phase" in context:
        phase = context["bayer_phase"]
        parts.append(f"CFA colour at this position is "
                     f"[{(row & 1) ^ ((phase >> 1) & 1)}]"
                     f"[{(col & 1) ^ (phase & 1)}]")
    return "; ".join(parts)


@cocotb.test()
async def bit_exact_against_model(dut):
    """The generated Verilog must produce EXACTLY what the NumPy model produces.

    Not close. Identical, pixel for pixel and flag for flag, for every trial
    against ONE elaboration -- registers and context are runtime state, so a
    single bitstream must be right for all of them -- under randomised
    backpressure on both sides.
    """
    case = _load_case()
    width, height = case["width"], case["height"]
    bit_depth = case["bit_depth"]
    block = resolve(case["block"]).configure(case.get("registers"))
    # Raw two's complement at each register's DECLARED width -- the testbench
    # holds no mask of its own to go stale when a design overrides bits.
    reg_bits = {r.name: r.param.bits for r in block.params.registers}

    cocotb.start_soon(Clock(dut.clk, 10, unit="ns").start())
    await reset_stream(dut)

    for trial_number, trial in enumerate(case["trials"]):
        rnd = random.Random(trial["seed"])
        frame = np.array(trial["frame"], dtype=np.uint64).reshape(height, width)
        values = trial.get("values", {})
        context = trial.get("context", {})

        for name, value in values.items():
            getattr(dut, f"param_{name}").value = (
                int(value) & ((1 << reg_bits[name]) - 1))
        # Context reaches the datapath as the single-bit ports the block
        # DECLARED -- which bit of which pipeline register feeds which port
        # is the ContextBit's statement, not this file's.
        for bit in block.context:
            getattr(dut, f"param_{bit.name}").value = (
                int(context[bit.context]) >> bit.bit) & 1
        await RisingEdge(dut.clk)

        # Models speak channels ((h, w, c), the NumPy way); the wire speaks
        # words. The packing law's owner translates at BOTH boundaries.
        model_in = frame
        # The case states the channel count of the words it packed, right
        # next to the spec the DUT was generated with -- one writer, both
        # facts. There is no declaration to consult: streams carry whatever
        # the upstream math packed.
        in_channels = case.get("in_channels", 1)
        if in_channels > 1:
            spec_in = StreamSpec(bit_depth=bit_depth, channels=in_channels)
            model_in = np.array(
                [[spec_in.unpack(int(word)) for word in row]
                 for row in frame], dtype=np.int64)
        expected = block.run(model_in, values, bit_depth=bit_depth, **context)
        if expected.ndim == 3:
            spec_out = StreamSpec(bit_depth=bit_depth,
                                  channels=expected.shape[-1])
            expected = np.apply_along_axis(
                lambda px: spec_out.pack(px.tolist()), -1,
                expected.astype(np.int64)).astype(np.uint64)

        # The wire carries the model's own input words; the framing they get
        # is np2hw's statement, made where the flags' meaning lives.
        beats = frame_to_beats(frame.tolist())
        collected = await run_frame(dut, beats, expected.size, rnd,
                                    drive=("sof",))

        label = f"trial {trial_number} ({trial.get('label', '')}: " \
                f"context={context})"
        assert len(collected) == expected.size, (
            f"{label}: DUT produced {len(collected)} pixels, model produced "
            f"{expected.size}")
        flat = expected.ravel()
        for i, beat in enumerate(collected):
            if beat.data != int(flat[i]):
                raise AssertionError(
                    f"{label}: " + _diagnose(block, case, trial, i,
                                             beat.data, int(flat[i]), frame))
        check_framing(collected, width, height)

        dut._log.info(f"{label}: {expected.size} pixels bit-exact, "
                      "framing exact")
