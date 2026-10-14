# Copyright 2026 Serge Rabyking
# SPDX-License-Identifier: Apache-2.0 WITH SHL-2.1
"""A 7-series MMCM recipe, held to what the part can do.

A board states a recipe -- its input, the input divider, the feedback
multiplier in eighths (the part's own resolution, so the recipe is whole
numbers) -- and the output it wants. The output divider is then
arithmetic, and so is how fine the phase shifter steps that output: the
two facts a genlocked raster needs, from one place. A recipe that cannot
make the output exactly, or puts the VCO outside the part's range, is
refused here, before Vivado rounds it to something near.
"""

# The VCO's range at the -1 speed grade, and the fine phase shifter's
# resolution: one step is 1/56 of a VCO period (UG472).
VCO_KHZ = (600_000, 1_200_000)
# A PLL's VCO range at the same grade (DS187).
STEPS_PER_VCO_PERIOD = 56


def _divide(in_khz: int, divclk: int, mult8: int, out_khz: int) -> int:
    mult = mult8 / 8
    vco = in_khz * mult / divclk
    if not VCO_KHZ[0] <= vco <= VCO_KHZ[1]:
        raise ValueError(f"{in_khz} kHz / {divclk} x {mult} puts the VCO at "
                         f"{vco:g} kHz, outside {VCO_KHZ[0]}-{VCO_KHZ[1]}")
    divide = vco / out_khz
    # a fine-shifted output takes an integer divider
    if divide != int(divide):
        raise ValueError(f"a {vco:g} kHz VCO does not divide to {out_khz} "
                         f"kHz exactly ({divide:g}); state another recipe")
    return int(divide)


def _spell(value) -> str:
    return f"{value:.3f}"


def multiplier(mult8: int) -> str:
    """The feedback multiplier as Vivado's IP properties spell one: 297
    eighths is ``37.125``."""
    return _spell(mult8 / 8)


multiplier.expression = True


def output_divide(in_khz: int, divclk: int, mult8: int, out_khz: int) -> str:
    """The output divider the recipe makes `out_khz` with, spelled for
    the IP."""
    return _spell(_divide(in_khz, divclk, mult8, out_khz))


output_divide.expression = True


def whole_divide(in_khz: int, divclk: int, mult8: int, out_khz: int) -> int:
    """The recipe's divider for `out_khz`, as a whole number: for an
    output whose divider takes no fraction."""
    return _divide(in_khz, divclk, mult8, out_khz)


whole_divide.expression = True


def fine_steps(in_khz: int, divclk: int, mult8: int, out_khz: int) -> int:
    """Fine phase steps in one period of the output: what a raster
    steering this clock counts per pixel."""
    return STEPS_PER_VCO_PERIOD * _divide(in_khz, divclk, mult8, out_khz)


fine_steps.expression = True


def pixel_khz(mode: str) -> int:
    """A raster's pixel clock by its name (``1080p30``), from bayerlink's
    rasters: for a design's expression,
    ``"@revela.boards.common.clocking:pixel_khz(mode)"``."""
    from bayerlink.rasters import raster
    return raster(mode).pixel_khz


pixel_khz.expression = True


def row(table, khz: int) -> int:
    """Which row of a clock table makes `khz`: for a design's expression
    to reset a clock select to its default."""
    rows = [int(k) for k in table]
    if int(khz) not in rows:
        raise ValueError(f"{khz} kHz is not in the clock table {rows}")
    return rows.index(int(khz))


row.expression = True


def tmds_recipe(khz: int) -> dict:
    """The serializer MMCM's recipe for a pixel clock of `khz`: the
    smallest feedback multiplier that is a multiple of five and puts the
    VCO in its range, the x5 output's divider a fifth of it -- so the
    output is five times the input at every clock. Refused where no
    multiplier does."""
    for mult in range(5, 65, 5):
        if VCO_KHZ[0] <= khz * mult <= VCO_KHZ[1]:
            return {"divclk": 1, "mult": mult, "out0": mult // 5}
    raise ValueError(f"no serializer PLL recipe makes 5 x {khz} kHz")
