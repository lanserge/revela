# Copyright 2026 Serge Rabyking
# SPDX-License-Identifier: Apache-2.0 WITH SHL-2.1
"""The raster into the transmitter's pixel clock, simulated: at every
phase the divider can start on, every beat arrives once, in order, a few
beats behind the writer."""
import shutil
import subprocess
from pathlib import Path

import pytest

COMMON = Path(__file__).resolve().parents[1] / "revela" / "boards" / "common"

pytestmark = pytest.mark.skipif(shutil.which("iverilog") is None,
                                reason="needs iverilog")


def test_every_beat_crosses_once_in_order_at_any_phase(tmp_path):
    vvp = tmp_path / "tb.vvp"
    subprocess.run(["iverilog", "-g2012", "-o", str(vvp),
                    str(COMMON / "sim" / "tb_video_cdc.v"),
                    str(COMMON / "hdl" / "video_cdc.v")],
                   check=True, capture_output=True, text=True)
    run = subprocess.run(["vvp", str(vvp)], capture_output=True, text=True,
                         timeout=300)
    assert "video_cdc: all checks passed" in run.stdout, run.stdout[-3000:]
