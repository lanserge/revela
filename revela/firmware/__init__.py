# Copyright 2026 Serge Rabyking
# SPDX-License-Identifier: Apache-2.0 WITH SHL-2.1
"""The ISP's firmware contract: what a platform lends the firmware
(`revela_hal.h`) and the actions the firmware offers whoever drives it
(`revela_fw.h`). Portable C, no operating system: the same firmware runs
on a Pi beside the board under test and on the MCU of an ISP+MCU system.
"""
from pathlib import Path

HERE = Path(__file__).resolve().parent
