# Tang Primer 20K

Sipeed Tang Primer 20K — Gowin GW2A-18, 20736 LUT4, 828 Kb block RAM.

**Nothing here yet.** This directory is for the board-specific parts: pin
constraints, the clocking and reset scheme, the sensor's MIPI or DVP receiver,
and the project file for Gowin's toolchain or for the open-source
Yosys/nextpnr-himbaechel flow.

## Why this board is the interesting target

It is the headless case. There is no CPU, so there is nobody to run the 3A loops
or to bring the sensor up over I2C, and that is exactly the situation the sensor
descriptions' `register_sequences` are meant for: compile them into a ROM plus
the state machine that walks it, and the board configures its own sensor at power
on. That is the one place sensor data becomes gateware, and it is a good
demonstration that np2hw reaches past the datapath.

It is also the constrained case. 828 Kb of block RAM is not a lot once line
buffers are counted, and revela's block-per-buffer architecture is deliberately
not the cheapest way to spend it — see `docs/design-rules.md` on why that trade
is made, and on the route to fusing a sub-chain into one traced expression if
this board is what forces the issue.

## What is needed

- Pin constraints for the sensor connector and the HDMI or LCD output.
- A DVP or MIPI receiver producing the stream interface in `revela/stream.py`:
  `valid`/`ready`/`data`/`sof`/`eol`/`last`.
- A control transport. With no CPU, the SPI transport in `revela/host/spi.py` is
  the intended one, so a host can still configure registers over four wires.
- The I2C init sequencer, generated from the sensor description.
