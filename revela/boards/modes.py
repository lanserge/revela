# Copyright 2026 Serge Rabyking
# SPDX-License-Identifier: Apache-2.0 WITH SHL-2.1
"""Facts of a video mode, for a design's expressions.

The mode table is np2hw's (np2hw.video_out); this reads it, so a design
can say ``"@revela.boards.modes:pixel_khz(mode)"`` where it needs a
raster's pixel clock, and no step beside the design computes it.
"""
from __future__ import annotations


def pixel_khz(mode: str) -> int:
    """The pixel clock of a mode by its name ('1080p30'), in kHz."""
    from np2hw.video_out import mode_timing
    return int(round(float(mode_timing(mode)["pixel_mhz"]) * 1000))


pixel_khz.expression = True
