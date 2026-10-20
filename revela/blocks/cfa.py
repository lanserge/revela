# Copyright 2026 Serge Rabyking
# SPDX-License-Identifier: Apache-2.0 WITH SHL-2.1
"""What a block indexing the 2x2 colour tile declares about itself.

A CFA block takes the tile's phase as one scalar input, and reads its two
bits as the starts of its stride-2 slices. The block knows nothing of where
the phase comes from: a design binds the input -- to the stream's
``bayer_phase`` where a header carries one -- and, unbound, it is the
block's own register.
"""
from __future__ import annotations

from np2hw.blocks.params import Param

from revela.blocks.context import BAYER_RGGB


def phase_input() -> Param:
    """The CFA phase: bit 1 the row parity R sits on, bit 0 its column
    parity -- the four combinations the four CFA orders."""
    return Param(
        name="bayer_phase",
        bits=2,
        default=BAYER_RGGB,
        description="Position of the R pixel in the 2x2 CFA tile: bit 1 row "
                    "parity, bit 0 column parity. RGGB=0, GRBG=1, GBRG=2, "
                    "BGGR=3",
    )
