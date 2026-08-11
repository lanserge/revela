# Copyright 2026 Serge Rabyking
# SPDX-License-Identifier: Apache-2.0 WITH SHL-2.1
"""Malvar-He-Cutler demosaic: gradient-corrected linear interpolation.

INTENT -- not implemented, and DATED: the method is claimed by Microsoft's
US7502505B2, which is in force until its adjusted expiration on
2027-01-25. This project ships silicon IP under a commercial tier with
indemnity, so the implementation waits for that date -- not because
enforcement is likely, but because "unenforced" is not "licensed".
Verified 2026-08-11 (patents.google.com/patent/US7502505B2).

The observation that makes this work: at a pixel where G was measured but R was
not, the LOCAL SECOND DERIVATIVE of G is a good estimate of the local second
derivative of R, because edges are a property of the scene rather than of one
colour channel. So interpolate R bilinearly, then correct it by a multiple of
G's Laplacian at that point.

    R_estimate = bilinear(R) + alpha * laplacian(G)

A 5x5 window, four line buffers, and coefficients that are all multiples of 1/8
-- so the whole thing is integer multiply-accumulate followed by a shift by 3,
with an explicit rounding constant, and a final clamp.

The quality gain over bilinear is large and the cost over bilinear is small,
which is why this is the usual default in production silicon. It remains a
LINEAR filter, so it cannot fully resolve fine diagonal detail: that is what
menon is for.

Reference: Malvar, He and Cutler, "High-quality linear interpolation for
demosaicing of Bayer-patterned color images", ICASSP 2004. The coefficients are
published in that paper and are derived from it, not transcribed from any
existing implementation.
"""


def model(*args, **kwargs):
    """Not implemented yet. See the module docstring for the intended design."""
    raise NotImplementedError(
        "malvar is a declared stub: its intent is documented, its model is not "
        "written. Implementing it means writing the NumPy model at the "
        "hardware's arithmetic FIRST, then generating from it -- not the other "
        "way round. See docs/design-rules.md.")
