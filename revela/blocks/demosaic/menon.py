# Copyright 2026 Serge Rabyking
# SPDX-License-Identifier: Apache-2.0 WITH SHL-2.1
"""Menon directional demosaic with refinement.

INTENT -- not implemented, and CLEARED: patent-checked 2026-08-11. The
authors published DDFAPD openly (IEEE TIP 2007) and filed nothing; the
foundational directional interpolation (Hamilton-Adams, US5629734)
expired 2015; the nearest in-class patent (US7643676, adaptive
directional decision) ran out 2026-06-23; and anything that could
validly claim the published method predates the 2007 paper, so its
base term is already over. Implement from the PAPER -- never from the
reference code or another project's implementation.

Linear filters must blur across edges because they cannot know where the edges
are. This one decides first and interpolates second:

  1. Interpolate G twice -- once using only horizontal neighbours, once using
     only vertical.
  2. For each candidate, form the chrominance differences (G - R, G - B).
  3. Choose per pixel whichever direction gives the LOCALLY SMOOTHER
     chrominance. Chrominance is smooth in real scenes, so the direction that
     produces the smoother chrominance is the one that interpolated ALONG the
     edge rather than across it.
  4. Reconstruct R and B using the chosen direction, then refine.

The best quality of the three on fine diagonal detail and on the resolution
targets people photograph to compare demosaics. The costs are real: roughly
double the line buffering, two full interpolation paths, and a decision network
whose bit-exact verification is a serious piece of work -- the direction
decision is a comparison, so a one-LSB difference in the criterion flips a
pixel's whole reconstruction, which makes this the block where rule 3 earns its
keep.

Reference: Menon, Andriani and Calvagno, "Demosaicing with directional filtering
and a posteriori decision", IEEE TIP 2007.
"""


def model(*args, **kwargs):
    """Not implemented yet. See the module docstring for the intended design."""
    raise NotImplementedError(
        "menon is a declared stub: its intent is documented, its model is not "
        "written. Implementing it means writing the NumPy model at the "
        "hardware's arithmetic FIRST, then generating from it -- not the other "
        "way round. See docs/design-rules.md.")
