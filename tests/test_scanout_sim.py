# Copyright 2026 Serge Rabyking
# SPDX-License-Identifier: Apache-2.0 WITH SHL-2.1
"""The raster behind its register file, simulated: its mode and placement
taken whole, in blanking, across the clock gap; a bus that answers with
the raster's clock gone; a mode change scanned at the new totals."""
import shutil
import subprocess
from pathlib import Path

import pytest

from revela.boards.common.scanout import scanout_regs

COMMON = Path(__file__).resolve().parents[1] / "revela" / "boards" / "common"

pytestmark = pytest.mark.skipif(shutil.which("iverilog") is None,
                                reason="needs iverilog")


def test_the_raster_takes_its_mode_and_placement_from_the_bus(tmp_path):
    pytest.importorskip("bayerlink")
    regs = scanout_regs("1080p60", 280)
    (tmp_path / "scanout_regs.v").write_text(regs["verilog"])
    # the file's own addresses, so the bench restates none of them
    offsets = regs["facts"]["offsets"]
    (tmp_path / "scanout_regs_map.vh").write_text("".join(
        f"  localparam [7:0] A_{name.upper()} = 8'h{off:02x};\n"
        for name, off in offsets.items())
        + f"  localparam [31:0] LAYOUT_ID = 32'h{regs['facts']['id']:08x};\n")
    vvp = tmp_path / "tb.vvp"
    subprocess.run(["iverilog", "-g2012", "-I", str(tmp_path), "-o", str(vvp),
                    str(COMMON / "sim" / "tb_scanout_regs.v"),
                    str(tmp_path / "scanout_regs.v"),
                    str(COMMON / "hdl" / "scanout.v"),
                    str(COMMON / "hdl" / "raster.v")],
                   check=True, capture_output=True, text=True)
    run = subprocess.run(["vvp", str(vvp)], capture_output=True, text=True,
                         timeout=1200)
    assert "scanout: all checks passed" in run.stdout, run.stdout[-3000:]
