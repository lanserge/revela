# Copyright 2026 Serge Rabyking
# SPDX-License-Identifier: Apache-2.0 WITH SHL-2.1
"""Design packs of revela's pipelines.

The pack emitter and the generator protocol are np2hw's and are tested
there. What is under test here is revela's: the stereo ISP's line buffers
all sit behind np2hw's memory seam, and the pipeline reader example reads
designs rather than building them a second way.
"""
from __future__ import annotations

import json
from pathlib import Path

from np2hw.pipeline import pack

ROOT = Path(__file__).resolve().parent.parent
# The shipped design: two standard ISPs, demosaic line buffers and all.
STEREO = "revela.pipelines.stereo:revela_stereo"


def test_the_explainer_is_not_a_second_emitter():
    """examples/build_pipeline.py must READ designs, not build them.

    Two paths that both write Verilog are two ways for one design to
    come out differently, so the explainer goes through emit like every
    other caller. Structural, because the failure it guards against is
    someone adding a generate() call back for convenience.
    """
    source = (ROOT / "examples" / "build_pipeline.py").read_text()
    assert "pack.emit(" in source
    for forbidden in (".generate(", "write_register_map(", "write_systemrdl(",
                      "register_map_markdown("):
        assert forbidden not in source, (
            f"the explainer calls {forbidden} itself -- artifacts come from "
            "np2hw.pipeline.pack.emit, or there are two emitters again")


def test_sync_memory_admits_the_isp_and_publishes_its_memories(tmp_path):
    """Every memory the ISP infers sits behind np2hw's seam.

    ``sync_memory=True`` is the promise a pack bound for silicon makes: no
    line buffer reads combinationally, so each can be bound to a macro by
    module name. The demosaic pipeline is the one that infers memories at
    all, so it is the one that has to pass -- and the build report has to
    say what was inferred, because an integrator binds against the report,
    not against a grep of the Verilog.
    """
    written = pack.emit(STEREO, tmp_path, verify=False, sync_memory=True)
    memories = json.loads(written["build"].read_text())["memories"]
    assert memories, "a demosaic pipeline infers line buffers"
    entries = [m for stage in memories.values() for m in stage]
    assert all(m["read"] == "sync" for m in entries)
    assert all(m["module"].endswith("_linebuf") for m in entries)
    verilog = written["verilog"].read_text()
    for entry in entries:
        assert f"module {entry['module']} #(" in verilog

    # the flag refuses; it never changes what is emitted
    plain = pack.emit(STEREO, tmp_path / "plain", verify=False)
    assert plain["verilog"].read_text() == verilog
