# Copyright 2026 Serge Rabyking
# SPDX-License-Identifier: Apache-2.0 WITH SHL-2.1
"""Sharpen: restore the acutance lost to the optical stack and demosaic.

INTENT -- not implemented.

Every stage before this one loses high-frequency detail: the lens, the optical
low-pass filter if fitted, the pixel aperture, and demosaic's interpolation.
Sharpening puts back the appearance of that detail.

Intended design, an unsharp mask on LUMA only:

    detail    = y - blur(y)                      # 5x5 or 3x3 low-pass
    boosted   = (detail * strength) >> 8
    coring    = |detail| < threshold ? 0 : boosted
    out       = clip(y + coring, 0, full_scale)

Three things make this a real block rather than a one-line filter:

CORING -- below a threshold, detail is noise, and amplifying it is the single
most common way to make an image look worse while measuring as sharper. The
threshold is a register and should track the analogue gain.

ASYMMETRIC LIMITS -- overshoot on the bright side of an edge is far more visible
than undershoot, so positive and negative excursions get separate clamps.

LUMA ONLY -- sharpening chroma produces coloured fringes on edges for no
perceptual gain, which is why this sits after rgb2yuv.

np2hw already traces the unsharp structure (see its examples/isp/sharpen.py) and
hash-conses the shared blur, so the datapath is close to expressible today; the
coring and asymmetric limits are the parts that need thought.

Ordering: after rgb2yuv, operating on Y.
"""


def model(*args, **kwargs):
    """Not implemented yet. See the module docstring for the intended design."""
    raise NotImplementedError(
        "sharpen is a declared stub: its intent is documented, its model is not "
        "written. Implementing it means writing the NumPy model at the "
        "hardware's arithmetic FIRST, then generating from it -- not the other "
        "way round. See docs/design-rules.md.")
