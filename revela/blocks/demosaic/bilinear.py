# Copyright 2026 Serge Rabyking
# SPDX-License-Identifier: Apache-2.0 WITH SHL-2.1
"""Bilinear demosaic: separable linear interpolation per colour plane.

INTENT -- not implemented. See revela/blocks/demosaic/__init__.py.

The baseline algorithm. For each missing colour at a pixel, average the nearest
measured samples of that colour:

    G at an R or B site   -> average of the 4 orthogonal G neighbours
    R at a B site         -> average of the 4 diagonal R neighbours
    R at a G site         -> average of the 2 horizontal or vertical R neighbours

All divisors are 2 or 4, so every division is a shift and there is no rounding
decision to get wrong. Two line buffers, a 3x3 window, integer throughout.

It is included because it is the honest floor: it is what "just interpolate"
looks like, and having it in the tree makes the case for the better algorithms
concrete rather than asserted. Its weakness is structural, not a tuning problem
-- interpolating each channel independently ignores inter-channel correlation,
so it zippers on any edge that is not axis-aligned.
"""


def model(*args, **kwargs):
    """Not implemented yet. See the module docstring for the intended design."""
    raise NotImplementedError(
        "bilinear is a declared stub: its intent is documented, its model is not "
        "written. Implementing it means writing the NumPy model at the "
        "hardware's arithmetic FIRST, then generating from it -- not the other "
        "way round. See docs/design-rules.md.")
