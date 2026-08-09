# Copyright 2026 Serge Rabyking
# SPDX-License-Identifier: Apache-2.0 WITH SHL-2.1
"""RGB to YUV: convert to a luma/chroma representation for output or encoding.

INTENT -- not implemented.

Video encoders and most display links want luma and chroma, not RGB, because
chroma can be subsampled -- the eye's spatial acuity for colour is far below its
acuity for brightness -- and because luma is what sharpening should operate on.

Intended design, a fixed 3x3 matrix with offsets:

    y = ((  66*r + 129*g +  25*b + round) >> 8) + 16
    u = (( -38*r -  74*g + 112*b + round) >> 8) + 128
    v = (( 112*r -  94*g -  18*b + round) >> 8) + 128

for Rec.601 limited range. The coefficients are registers rather than constants
so that Rec.709 and full-range variants are a register write rather than a
rebuild -- the same reasoning as Bayer phase, and the same cost argument: nine
small registers are nothing next to a second bitstream and a second
verification run.

The offsets (16, 128) are part of the standard, not an implementation detail:
limited-range video reserves headroom and footroom for filter overshoot.

Chroma subsampling to 4:2:2 or 4:2:0, if wanted, is a separate block: it changes
the stream's shape rather than its values, and mixing the two makes both harder
to verify.

Ordering: last, or second to last if sharpening operates on luma.
"""


def model(*args, **kwargs):
    """Not implemented yet. See the module docstring for the intended design."""
    raise NotImplementedError(
        "rgb2yuv is a declared stub: its intent is documented, its model is not "
        "written. Implementing it means writing the NumPy model at the "
        "hardware's arithmetic FIRST, then generating from it -- not the other "
        "way round. See docs/design-rules.md.")
