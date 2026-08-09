# Copyright 2026 Serge Rabyking
# SPDX-License-Identifier: Apache-2.0 WITH SHL-2.1
"""Demosaic: reconstruct three colours per pixel from the CFA mosaic.

NOT IMPLEMENTED -- deliberately. The algorithms live in submodules so that they
can be compared honestly against each other, and so that a pipeline picks one at
composition time rather than at runtime.

The sensor measures ONE colour per pixel. Demosaic estimates the other two. It
is the single largest determinant of perceived image quality in the whole
pipeline, and the place where naive algorithms produce the artefacts everyone
recognises: zippering on horizontal edges, coloured fringes on high-contrast
detail, and maze patterns in fine texture.

Submodules, in increasing order of cost and quality:

    bilinear    Separable linear interpolation within each colour plane. Two
                line buffers. The baseline: cheap, and visibly wrong on edges,
                because interpolating each channel independently ignores that
                edges are correlated ACROSS channels.

    malvar      Malvar-He-Cutler gradient-corrected linear interpolation. A 5x5
                filter that corrects each channel's estimate using the
                Laplacian of the channel that WAS measured at that pixel. Four
                line buffers, all integer coefficients over 8, and a very large
                quality gain for the cost -- the usual default.

    menon       Directional filtering with refinement: interpolate along the
                horizontal and vertical directions separately, then choose per
                pixel using a chrominance-gradient criterion. Best quality of
                the three, notably on fine diagonal detail, at roughly double
                the buffering and a real decision network.

Choosing between them is exactly the kind of question experiments/ exists for --
compared at PIPELINE level against reference images, never per block. Per-block,
each is held bit-exact to its own model, which says nothing about which looks
better and is not meant to.

All three consume `bayer_phase` from the pipeline context, so one bitstream
serves any CFA order.
"""


def model(*args, **kwargs):
    """Not implemented yet. See the module docstring for the intended design."""
    raise NotImplementedError(
        "__init__ is a declared stub: its intent is documented, its model is not "
        "written. Implementing it means writing the NumPy model at the "
        "hardware's arithmetic FIRST, then generating from it -- not the other "
        "way round. See docs/design-rules.md.")
