# Copyright 2026 Serge Rabyking
# SPDX-License-Identifier: Apache-2.0 WITH SHL-2.1
"""The ``pipe`` block: pipeline context, owned in one place and fanned out.

Width, height, the Bayer phase and the bit depth are facts about the
pipeline, not about any one block. They are declared here, once, and reach
the blocks a design binds to them as WIRES. No other block holds a copy. A
fact the design names in ``stream.context`` arrives with the stream instead,
and takes no register here -- the standard ISP takes width, height, Bayer
phase and depth that way, leaving ``pipe`` only its commit.

That is worth being firm about. The tempting alternative -- give every block its
own width register -- means a resolution change is N register writes that must
all land in the same frame, and any block whose write is missed corrupts the
image in a way that looks like a bug in that block. One owner, fanned out, makes
that class of failure impossible to express.

``pipe`` is an ordinary block. It has a ParamSet, an ID-and-version word at local
offset 0, and it gets a base address from the allocator like everything else. It
is placed at base 0 by convention, because a host bringing up an unknown
bitstream has to start reading somewhere, but nothing in the code special-cases
it: the composer allocates it through the same path as the rest.

Why these are run-time facts and not build-time constants
---------------------------------------------------------

Bayer phase is two bits. Making it run-time -- a register, or the stream's own
header -- costs almost nothing and lets ONE
bitstream serve every sensor, every flip, and every mirror setting. Generating a
different pipeline per sensor would turn the verification matrix into sensors x
modes, and a matrix like that does not stay green. Only the depth samples
arrive at and the widest and tallest frame are build-time, because they are
structural: the datapath's width and the line buffers' size.
"""
from __future__ import annotations

from np2hw.blocks import configblock
from np2hw.blocks.params import Param, Vocabulary

from revela.blocks.context import (  # noqa: F401  (the pipe's own context)
    BAYER_BGGR, BAYER_GBRG, BAYER_GRBG, BAYER_ORDER_CODES, BAYER_ORDER_NAMES,
    BAYER_RGGB, CONTEXT, CONTEXT_BY_NAME, bayer_phase_of, context_names,
    resolve)

# No stream ports: `pipe` owns configuration and fans context out as wires. It is
# still an ordinary block -- allocated an address like any other, with an
# ID-and-version word -- it simply has no ports to wire.
# `commit` is NOT context: it is never fanned out to a block. It is how
# software says "the values I have been writing are complete, take them"
# -- and it is what makes the coefficients safe to read from the
# datapath's clock, which is not the clock this file runs on.
#
# The register file lives on the processor's clock, because one on the
# pixel clock has no clock whenever the link is down and an AXI slave
# that cannot answer hangs the processor that asked (2026-08-17: it did).
# Reading those values from the pixel clock is therefore a crossing, and
# a wide value sampled mid-write is half old bits and half new. While
# this bit is set the file REFUSES coefficient writes, so the values
# provably cannot move while the datapath copies them; the datapath
# clears it when it has.
#
# It also makes a batch atomic. Writing five of nine matrix coefficients
# either side of a frame boundary otherwise applies half a matrix.
COMMIT = Param(
    name="commit",
    bits=1,
    default=0,
    description="Write 1 when a batch of coefficient writes is complete. "
                "Coefficient writes are refused (SLVERR) while it is set, "
                "and the datapath clears it once it has taken the values -- "
                "so poll it to know the update has landed. Reads back 1 "
                "while still pending",
)

pipe = configblock(
    "pipe",
    version=(1, 0),
    description="Pipeline-wide context, fanned out to blocks as wires.",
    # A stream-only context has no register -- nothing software writes could
    # keep up with it -- so it takes no address, and the map of every
    # pipeline that does not use it is untouched.
    params=[ctx.as_param() for ctx in CONTEXT if ctx.origin != "stream"]
    + [COMMIT],
)

#: revela's pipeline context, with the block that owns it: what every
#: revela block is declared with, and what places ``pipe`` at base 0 in
#: every pipeline built of them.
VOCABULARY = Vocabulary(CONTEXT, block=pipe, commit="commit")
