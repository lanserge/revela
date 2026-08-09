# Copyright 2026 Serge Rabyking
# SPDX-License-Identifier: Apache-2.0 WITH SHL-2.1
"""Defect pixel correction: replace pixels the sensor cannot read.

INTENT -- not implemented.

Every sensor ships with some pixels stuck bright, stuck dark, or too noisy to
use. They are individually invisible but demosaic smears each one across its
neighbourhood, turning a single bad pixel into a coloured blob.

Two mechanisms, and they are genuinely different:

STATIC -- a map of known-bad coordinates from the sensor's factory test or from
calibration. Exact, but it is PER-UNIT CALIBRATION data and does not live in
this repo. Stored sorted by raster order so the hardware compares the incoming
coordinate against one pointer rather than searching.

DYNAMIC -- detect outliers on the fly by comparing a pixel with its same-colour
neighbours in a 5x5 window (same-colour because the neighbours must be
comparable; in a Bayer frame that means a stride of two). If the pixel lies
outside the neighbourhood's range by more than a programmable threshold,
replace it with the median of those neighbours.

The dynamic path needs no calibration and catches defects that develop in the
field, but it can misfire on genuine single-pixel detail -- a star field, a
specular highlight. Hence the threshold is a register, and the two mechanisms
are independently enableable.

Intended arithmetic: integer min/max/median of a same-colour 5x5 neighbourhood
(a sorting network, not a sort), threshold compare, and a 2:1 select.

Ordering: after black level, before demosaic -- correcting a defect after
demosaic means correcting the blob it already became.
"""


def model(*args, **kwargs):
    """Not implemented yet. See the module docstring for the intended design."""
    raise NotImplementedError(
        "defect is a declared stub: its intent is documented, its model is not "
        "written. Implementing it means writing the NumPy model at the "
        "hardware's arithmetic FIRST, then generating from it -- not the other "
        "way round. See docs/design-rules.md.")
