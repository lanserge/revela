# Copyright 2026 Serge Rabyking
# SPDX-License-Identifier: Apache-2.0 WITH SHL-2.1
"""Per-zone statistics for auto exposure and auto white balance.

The 3A loops do not need the image. They need a small, reliable summary of it,
once per frame, cheap enough to read over a slow control link. This block divides
the active window into a grid of zones and accumulates, per zone, the sum of each
CFA colour and the pixel count.

Structure, and why it is not a pile of CSRs
-------------------------------------------

A 16x16 grid with five words per zone is 1280 words. Exposing that as
individually addressed configuration registers would be absurd -- the host wants
to read the whole thing in one burst, and it wants a COHERENT snapshot. So the
statistics live in their own address region as a memory-mapped RAM window, and
the window is DOUBLE-BUFFERED: the host reads frame N while frame N+1
accumulates into the other buffer, and the buffers swap at end of frame.

This is deliberately the opposite mechanism to configuration. Config uses shadow
registers committed at the frame boundary, because the host is writing and the
hardware must not see a half-updated set. Statistics have the hardware writing
and the host reading, so the problem is a torn read, and the fix is a second
buffer. Using commit-on-vsync here would solve the wrong problem.

Luminance
---------

Luminance is DERIVED from the colour sums at end of frame, not accumulated
per-pixel:

    sum_y = (sum_r * weight_r + sum_g * weight_g + sum_b * weight_b) >> 8

Accumulating a per-pixel ``(pixel * weight) >> 8`` instead would put a multiplier
in the pixel path -- the one place in the design where a multiplier is expensive
-- and would throw away up to eight bits of precision per pixel, which over
thousands of pixels per zone is a real bias, not rounding noise. Deriving it from
the sums is exact, and costs one multiply-accumulate per zone during blanking,
shared across the whole grid.

Zone boundaries
---------------

Zones are delimited by a programmable ``zone_width`` and ``zone_height`` in
pixels, counted by comparators, rather than by dividing the frame. A divider in
the pixel path to compute ``x * zones / width`` would be silly when the host can
write ``width // zones`` once per resolution change. Pixels past the last whole
zone are excluded from the statistics rather than being folded into the final
zone, so every zone covers exactly the same area and the 3A loops can compare
zone sums directly without weighting them.

Generation status
-----------------

The model below is the specification, and it is written at the hardware's
arithmetic. The Verilog is NOT yet generated: np2hw traces stencils and pointwise
operations, and accumulation over a region is a reduction, which it does not
trace yet. Until it does, this block has a model and a register map but no
bit-exact test, and it is the one block in revela that does not satisfy rule 3.
That is stated here rather than hidden behind a passing test that only exercises
the NumPy side.
"""
from __future__ import annotations

import numpy as np

from revela.blocks import BAYER, StreamPort, ispblock
from revela.params import Param, StatsWindow

# Build-time grid maximum. The RAM is sized for this; the zone SIZE is a runtime
# register, so one bitstream covers every resolution with the same grid.
ZONES_X = 16
ZONES_Y = 16

# One 32-bit word per field. A 1920x1080 frame in 256 zones is ~8100 pixels per
# zone, ~2000 per CFA colour; at 12 bits that is a 24-bit sum, so 32 bits leaves
# headroom for 16-bit sensors without a second word.
ACCUMULATOR_BITS = 32

# Luminance weights are Q0.8. Integers, because the hardware has integers.
LUMA_SHIFT = 8

# Samples of each colour per pixel of a zone, as a divisor: a Bayer tile is one
# R, two G and one B, so a zone of N pixels holds N/4 red samples, N/2 green and
# N/4 blue. Anything comparing the colour sums to each other MUST account for
# this -- the raw green sum is roughly twice the red sum for a neutral scene, and
# treating them as comparable makes a white balance loop converge to the wrong
# answer while looking perfectly well behaved.
CFA_SAMPLE_DIVISOR = (4, 2, 4)

STATS_LAYOUT = ("sum_r", "sum_g", "sum_b", "sum_y", "count")

STATS_DECLARATION = dict(
    version=(1, 0),
    name="stats",
    description="Per-zone CFA colour and luminance statistics for AE and AWB.",
    consumes=("bayer_phase", "window_x0", "window_y0", "window_x1", "window_y1"),
    # A SINK: statistics observe the datapath and produce no pixels, so this
    # block taps the stream rather than sitting in it and never stalls its
    # source -- the pixels it is watching have somewhere else to be.
    inputs=(StreamPort("in", BAYER,
                       "The pedestal-corrected Bayer stream being metered."),),
    outputs=(),
    not_traceable=(
        "accumulation over a region is a reduction, and np2hw traces stencils "
        "and pointwise operations but not reductions yet"),
    params=[
        Param(
            name="zone_width",
            bits=16,
            default=120,
            description="Width of one statistics zone in pixels. Set to "
                        "(window width // zones_x); pixels past the last whole zone are "
                        "excluded so every zone covers an equal area",
        ),
        Param(
            name="zone_height",
            bits=16,
            default=67,
            description="Height of one statistics zone in lines. Set to "
                        "(window height // zones_y)",
        ),
        Param(
            name="zones_x",
            bits=8,
            default=ZONES_X,
            description=f"Zone columns in use, 1..{ZONES_X}. Fewer zones means more "
                        "pixels per zone and a quieter estimate",
        ),
        Param(
            name="zones_y",
            bits=8,
            default=ZONES_Y,
            description=f"Zone rows in use, 1..{ZONES_Y}",
        ),
        Param(
            name="luma_weight",
            bits=9,
            shape=(3,),
            default=0,
            description="Luminance weights for R, G, B as Q0.8; sum_y is derived from "
                        "the colour sums at end of frame. Defaults are written by the "
                        "host from LUMA_WEIGHT_DEFAULT",
        ),
    ],
    stats=[
        StatsWindow(
            name="zones",
            words=ZONES_X * ZONES_Y * len(STATS_LAYOUT),
            layout=STATS_LAYOUT,
            description="Per-zone accumulators, row-major, one record per zone. "
                        "Double-buffered: this window is the frame that has completed, "
                        "while the next frame accumulates into the other buffer",
        ),
    ],
)

# Rec.601 luma weights (54, 183, 18 of 255), PRE-SCALED by CFA_SAMPLE_DIVISOR so
# that sum_y comes out proportional to the zone's mean luminance rather than to
# whichever colour happens to have the most samples. Not a float anywhere: these
# ARE the register values, and the host writes them verbatim.
LUMA_WEIGHT_DEFAULT = (54 * 4, 183 * 2, 18 * 4)     # -> (216, 366, 72)


def default_registers(zones_x: int = ZONES_X, zones_y: int = ZONES_Y,
                      window: tuple[int, int, int, int] = (0, 0, 1920, 1080)) -> dict[str, int]:
    """Register values for a given window and grid.

    Computing ``zone_width`` on the host is the whole reason the hardware has no
    divider, so this is where that division lives.
    """
    x0, y0, x1, y1 = window
    values = {
        "zone_width": max(1, (x1 - x0) // zones_x),
        "zone_height": max(1, (y1 - y0) // zones_y),
        "zones_x": zones_x,
        "zones_y": zones_y,
    }
    for i, weight in enumerate(LUMA_WEIGHT_DEFAULT):
        values[f"luma_weight_{i}"] = weight
    return values


@ispblock(**STATS_DECLARATION)
def model(pixel: np.ndarray, p, bayer_phase: int = 0,
          window: tuple[int, int, int, int] | None = None) -> np.ndarray:
    """Accumulate one frame of statistics.

    THE specification and THE golden reference. Integer throughout, saturating
    where the hardware's accumulator saturates.

    Args:
        pixel: ``(height, width)`` raw Bayer frame, unsigned.
        p: bound parameter values, from ``model.params.bind({...})``.
        bayer_phase: pipeline context. Position of R: bit 1 row parity, bit 0
            column parity.
        window: ``(x0, y0, x1, y1)`` active window, exclusive at the top end.
            Defaults to the whole frame. Comes from pipeline context.

    Returns:
        A 2-D ``(zones_y * zones_x, 5)`` uint32 array: one ROW per zone record,
        one COLUMN per field of :data:`STATS_LAYOUT`, zones in row-major order.

        This is deliberately the memory's shape, not the picture's. Flattened
        row-major with ``.ravel()`` it IS the contents of one buffer of the
        ``zones`` window, word for word, in address order -- so checking the
        hardware against this model is a bulk read and a ``reshape(-1, 5)``, with
        no reindexing step in between that could hide a layout error. Use
        :func:`as_grid` for the spatial view the control loops want.
    """
    pixel = np.asarray(pixel)
    if pixel.ndim != 2:
        raise ValueError(f"expected a 2-D Bayer frame, got shape {pixel.shape}")
    height, width = pixel.shape
    x0, y0, x1, y1 = window if window is not None else (0, 0, width, height)
    if not (0 <= x0 < x1 <= width and 0 <= y0 < y1 <= height):
        raise ValueError(
            f"window {(x0, y0, x1, y1)} outside the {width}x{height} frame")

    zones_x, zones_y = int(p.zones_x), int(p.zones_y)
    zone_w, zone_h = int(p.zone_width), int(p.zone_height)
    if not 1 <= zones_x <= ZONES_X or not 1 <= zones_y <= ZONES_Y:
        raise ValueError(
            f"grid {zones_x}x{zones_y} exceeds the built maximum {ZONES_X}x{ZONES_Y}")

    phase_row, phase_col = (int(bayer_phase) >> 1) & 1, int(bayer_phase) & 1
    limit = (1 << ACCUMULATOR_BITS) - 1

    # Colour sums first. int64 during accumulation, saturated to the hardware's
    # 32-bit accumulator on the way out -- an unsaturated Python int would
    # silently model an accumulator the hardware does not have.
    sums = np.zeros((zones_y, zones_x, 4), dtype=np.int64)   # R, Gr, Gb, B
    count = np.zeros((zones_y, zones_x), dtype=np.int64)

    for dy in (0, 1):
        for dx in (0, 1):
            colour_row, colour_col = dy ^ phase_row, dx ^ phase_col
            colour = colour_row * 2 + colour_col          # 0=R 1=Gr 2=Gb 3=B

            # Rows and columns of this CFA phase that fall inside a whole zone.
            rows = np.arange(y0 + dy, y1, 2)
            cols = np.arange(x0 + dx, x1, 2)
            zy = (rows - y0) // zone_h
            zx = (cols - x0) // zone_w
            rows, zy = rows[zy < zones_y], zy[zy < zones_y]
            cols, zx = cols[zx < zones_x], zx[zx < zones_x]
            if rows.size == 0 or cols.size == 0:
                continue

            plane = pixel[np.ix_(rows, cols)].astype(np.int64)
            # np.add.at accumulates duplicate indices, which is what the
            # hardware's read-modify-write per pixel does.
            np.add.at(sums[:, :, colour], (zy[:, None], zx[None, :]), plane)
            np.add.at(count, (zy[:, None], zx[None, :]), np.ones_like(plane))

    sum_r = sums[:, :, 0]
    sum_g = sums[:, :, 1] + sums[:, :, 2]                  # Gr + Gb
    sum_b = sums[:, :, 3]

    weights = np.asarray(p.luma_weight, dtype=np.int64)
    sum_y = (sum_r * weights[0] + sum_g * weights[1] + sum_b * weights[2]) >> LUMA_SHIFT

    out = np.stack([sum_r, sum_g, sum_b, sum_y, count], axis=-1)
    out = np.minimum(out, limit).astype(np.uint32)
    # Collapse the grid to records: row-major zone index down, field across.
    # That is the RAM's own order, so .ravel() is the word sequence the host
    # reads back.
    return out.reshape(zones_y * zones_x, len(STATS_LAYOUT))


def as_grid(stats: np.ndarray, zones_x: int, zones_y: int) -> np.ndarray:
    """View the record array spatially: ``(zones_y, zones_x, 5)``.

    The control loops reason about where in the frame a zone is -- centre
    weighting for exposure, sky rejection for white balance -- so they want the
    picture's shape. The model returns the memory's shape; this is the bridge,
    and it is one reshape because the memory order was chosen to make it one.
    """
    stats = np.asarray(stats)
    expected = zones_y * zones_x
    if stats.shape != (expected, len(STATS_LAYOUT)):
        raise ValueError(
            f"expected a ({expected}, {len(STATS_LAYOUT)}) record array for a "
            f"{zones_x}x{zones_y} grid, got {stats.shape}")
    return stats.reshape(zones_y, zones_x, len(STATS_LAYOUT))


def field(stats: np.ndarray, name: str) -> np.ndarray:
    """One column of the record array, by name from :data:`STATS_LAYOUT`."""
    try:
        return np.asarray(stats)[:, STATS_LAYOUT.index(name)]
    except ValueError:
        raise KeyError(
            f"no statistics field {name!r}; the window holds {list(STATS_LAYOUT)}") from None


def colour_means(stats: np.ndarray) -> np.ndarray:
    """Per-SAMPLE mean of R, G and B in each zone: ``(zones, 3)``.

    This is what a white balance estimator must compare, not the raw sums. A
    Bayer tile has two green samples for every red and blue, so the green sum of
    a perfectly neutral scene is about twice the red sum. Dividing each colour by
    the number of samples it actually has -- see :data:`CFA_SAMPLE_DIVISOR` --
    makes the three comparable.

    Zones with no pixels come back as zero.
    """
    records = np.asarray(stats, dtype=np.int64)
    count = records[:, STATS_LAYOUT.index("count")]
    safe = np.maximum(count, 1)
    means = np.stack(
        [records[:, i] * CFA_SAMPLE_DIVISOR[i] // safe for i in range(3)], axis=-1)
    return np.where(count[:, None] > 0, means, 0)


def zone_means(stats: np.ndarray) -> np.ndarray:
    """Per-zone mean of each accumulated field, for the control loops.

    Integer division, matching what the host does with the raw sums -- the 3A
    loops run in integers too, so nothing here promotes to float. Zones with no
    pixels come back as zero rather than raising, because a partially covered
    grid is normal when the window does not divide evenly.

    Args:
        stats: the ``(zones, 5)`` record array from :func:`model`.

    Returns:
        ``(zones, 4)``: mean R, G, B and luminance per zone.
    """
    stats = np.asarray(stats, dtype=np.int64)
    count = stats[:, STATS_LAYOUT.index("count")]
    safe = np.maximum(count, 1)
    means = stats[:, :4] // safe[:, None]
    return np.where(count[:, None] > 0, means, 0).astype(np.int64)

