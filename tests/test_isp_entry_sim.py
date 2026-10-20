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


def test_the_facts_are_the_frames_and_a_wide_line_is_cropped(tmp_path):
    vvp = tmp_path / "tb.vvp"
    subprocess.run(["iverilog", "-g2012", "-o", str(vvp),
                    str(COMMON / "sim" / "tb_isp_entry.v"),
                    str(COMMON / "hdl" / "isp_entry.v")],
                   check=True, capture_output=True, text=True)
    run = subprocess.run(["vvp", str(vvp)], capture_output=True, text=True,
                         timeout=300)
    assert "isp entry: all checks passed" in run.stdout, run.stdout[-3000:]
