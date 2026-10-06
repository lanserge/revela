# Copyright 2026 Serge Rabyking
# SPDX-License-Identifier: Apache-2.0 WITH SHL-2.1
"""revela's pipe block holds a register for every context software or a
transport may set, and none for one the stream delivers every frame.

The rules of context origin are np2hw's and are tested there; this pins
revela's declaration of them.
"""
from revela.blocks import pipe as pipe_block


def test_a_stream_only_context_takes_no_register_anywhere():
    """No pipeline gets a register for ``frame_phase`` -- which is why adding
    it moved no address in any existing design."""
    registers = {p.name for p in pipe_block.pipe.params.params}
    assert "frame_phase" not in registers
    assert {c.name for c in pipe_block.CONTEXT if c.origin != "stream"} <= registers
