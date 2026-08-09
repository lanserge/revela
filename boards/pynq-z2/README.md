# PYNQ-Z2

TUL PYNQ-Z2 — Xilinx Zynq-7020, dual Cortex-A9 plus 85K logic cells, 630 KB
block RAM.

**Nothing here yet.** This directory is for the board-specific parts: the Vivado
block design, the AXI interconnect and address assignment, constraints, and the
overlay packaging.

## Why this board is the easy target

There is a CPU running Linux, so the 3A loops in `revela/control/` run where they
were designed to run, sensor bring-up over I2C is an ordinary userspace program,
and register access is memory-mapped through `revela/host/pynq.py`. Everything
that is awkward on a headless board is straightforward here, which makes this the
right board for bringing a new block up before porting it.

It is also where the AXI4-Stream Video adapter earns its place: VDMA and the
Xilinx video IP speak that protocol, so the adapter in `revela/stream.py` is what
connects revela's pipeline to a frame buffer and a display.

## What is needed

- A Vivado block design instantiating the generated top, with the pipeline's
  register aperture on the PS-PL AXI-Lite bus.
- The AXI4-Stream Video adapters at the pipeline's input and output, so VDMA can
  source and sink frames.
- Address assignment. Note the distinction that `revela/host/pynq.py` documents:
  revela's addresses are OFFSETS within the pipeline's aperture; where that
  aperture lands in the Zynq's address space is a platform fact from the block
  design, and adding the two is the transport's job.
- An overlay (`.bit` plus `.hwh`) and the packaging for `pynq.Overlay`.
