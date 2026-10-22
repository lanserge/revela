# Copyright 2026 Serge Rabyking
# SPDX-License-Identifier: Apache-2.0 WITH SHL-2.1
"""The ISP's entry, simulated: the header's facts held for the frame they
entered with, and a line wider than the ISP cropped, drained and said."""
import shutil
import subprocess
from pathlib import Path

import pytest

COMMON = Path(__file__).resolve().parents[1] / "revela" / "boards" / "common"

pytestmark = pytest.mark.skipif(shutil.which("iverilog") is None,
                                reason="needs iverilog")


def _layout():
    """The context the entry packs every header fact into, as revela's
    vocabulary lays it out: the parameters the bench is run at."""
    from np2hw.stream import context_bits
    from revela.blocks.pipe import VOCABULARY
    layout = VOCABULARY.layout(["width", "height", "bayer_phase",
                                "bit_depth", "frame_seq"])
    at, lo = {}, 0
    for name, bits, _ in layout:
        at[name], lo = lo, lo + bits
    return {"CTX_BITS": context_bits(layout), "AT_WIDTH": at["width"],
            "AT_HEIGHT": at["height"], "AT_PHASE": at["bayer_phase"],
            "AT_BIT_DEPTH": at["bit_depth"], "AT_FRAME_SEQ": at["frame_seq"]}


def test_the_facts_are_the_frames_and_a_wide_line_is_cropped(tmp_path):
    vvp = tmp_path / "tb.vvp"
    subprocess.run(["iverilog", "-g2012", "-o", str(vvp),
                    *(f"-Ptb.{k}={v}" for k, v in _layout().items()),
                    str(COMMON / "sim" / "tb_isp_entry.v"),
                    str(COMMON / "hdl" / "isp_entry.v")],
                   check=True, capture_output=True, text=True)
    run = subprocess.run(["vvp", str(vvp)], capture_output=True, text=True,
                         timeout=300)
    assert "isp entry: all checks passed" in run.stdout, run.stdout[-3000:]
