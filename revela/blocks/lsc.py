# Copyright 2026 Serge Rabyking
# SPDX-License-Identifier: Apache-2.0 WITH SHL-2.1
"""Lens shading correction: undo the lens's radial and colour falloff.

INTENT -- not implemented.

A lens delivers less light to the corners than the centre, and the falloff
differs per colour channel because the chief ray angle interacts with the CFA
and the microlenses. Uncorrected, every image has dark, colour-tinted corners.

Intended design:

    out = clip((pixel * gain[zone]) >> shift, 0, full_scale)

A coarse MESH of gain values -- typically 17x13 or 33x25 -- bilinearly
interpolated between mesh points as the raster advances, with a separate mesh
per CFA colour. The mesh is coarse because shading is smooth; interpolating is
far cheaper than storing a gain per pixel, and the interpolator is the real
content of this block.

Register interface: mesh dimensions build-time (they size the RAM), mesh values
loaded at runtime.

CRITICAL: the mesh is PER-UNIT CALIBRATION. It depends on the lens, the
assembly tolerances and the illuminant, and it does NOT belong in
revela/sensors/ or anywhere else in this repo. The block loads it at runtime.
See docs/design-rules.md.

Ordering: after black level (shading is multiplicative on a pedestal-free
signal; applying gain to a pedestal amplifies the pedestal into a visible
corner-brightening artefact) and before white balance.
"""


def model(*args, **kwargs):
    """Not implemented yet. See the module docstring for the intended design."""
    raise NotImplementedError(
        "lsc is a declared stub: its intent is documented, its model is not "
        "written. Implementing it means writing the NumPy model at the "
        "hardware's arithmetic FIRST, then generating from it -- not the other "
        "way round. See docs/design-rules.md.")
