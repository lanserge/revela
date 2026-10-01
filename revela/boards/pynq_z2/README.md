# PYNQ-Z2

**The revela ISP on silicon: raw Bayer in over HDMI, RGB out over HDMI.**

A camera source (picam2hdmi on a Raspberry Pi) sends raw sensor frames
over HDMI as [bayerlink](https://github.com/bayerlink/bayerlink). The
board's HDMI-in jack receives them, the ISP turns them into RGB, and a
raster drives HDMI out, genlocked to the camera. The board tests the ISP:
it reads the pipeline's published contract and never restates it.

| part | what it is | whose |
|---|---|---|
| receiver | `bayerlink_in`, the DDC slave, link reset, the dvi2rgb front end | [bayerlink-fpga](https://github.com/bayerlink/bayerlink-fpga) |
| jacks | `hdmi_in` (front end, its configuration here, pins) and `hdmi_out` (transmitter, pins): composites that own their cells, pins and constraints | this board |
| ISP | `isp.json` (`revela_isp_core`), a composite of revela's blocks, generated and proved bit-exact | this repository |
| raster, tee, grabber | np2hw's scanout, tee and FIFOs, the glue in `hdl/` | this board |
| the design | `pynq_demo.json`: every cell, connection and setting, JSON through and through; the block design is generated from it | this board, on revela's `zynq7` family library |

## Build

```sh
pip install -e ../bayerlink-fpga -e .      # from the checkouts; np2hw and
                                           # bayerlink come with them
git clone --depth 1 https://github.com/Digilent/vivado-library.git
git clone --depth 1 https://github.com/xupsh/pynq-supported-board-file.git board-files
sh "$(python3 -c 'import revela.boards.pynq_z2 as b; print(b.HERE)')/build.sh"
```

Run it from the directory the build should land in: every generated
file, the Vivado project and the bitstream go under `build/pynq_z2/`
there (`OUT=` overrides), and the vendor trees are found beside it
(`BOARD_FILES=`, `VIVADO_LIBRARY=`). The sources beside `build.sh` are
only ever read. `NO_VIVADO=1` stops after the generators and checks,
which takes seconds and needs no Vivado.

`build.sh` is the whole flow, and it exists because three of the four
sources are GENERATED and none of them is committed: the receiver, the
ISP and the display raster are emitted from their models, so a build
that skips a generator fails on a missing file rather than quietly
using a stale one. In order:

1. `python3 -m bayerlink_fpga <front end>`, the front end named by the
   `hdmi_in` jack, writes the receiver's Verilog.
2. `revela generate revela.boards.pynq_z2.isp:revela_isp_core` builds the
   ISP and proves it.
3. `scanout.py` and `tee.py` emit the raster and the output tee.
4. `blockdesign.py` loads `pynq_demo` -- supplying what only the build
   knows: the raster's pixel clock from the mode table, and the ISP's
   widths from its traced contract -- checks the board can honour it, and
   writes the block design. Every wire between our own modules is
   checked against the widths their RTL has with this build's
   parameters; a block design would connect the low bits of a mismatch
   and call it a warning. It runs after the ISP because the ISP's
   boundary is traced, so there is nothing to read until it is built.
5. Vivado: the generated `bd.tcl`, `impl_a.tcl` (synthesis, placement),
   `impl_b.tcl` (routing, bitstream); then `checkhwh` audits the design
   as built.

The GPIO words software reads and writes -- control bits, status, the
stream's identity -- are declared once, field by field, on `pynq_demo`'s
`control`, `status` and `stream` nodes. The fabric's GPIO cells, slices
and concatenations are derived from those declarations, and so is
`gpio_layout.json`, written beside `rx.bit`. Copy it to the board with the
bitstream, and `bench/layout.py` with the scripts: they read every field
by name through it, and write no bit position down. The layout's checksum
is built into the fabric as `ctrl_gpio`'s second channel, a constant, and
the file states it: a script reads it back before trusting the file, and
refuses a layout copied from another build.

A variant build is an edit to `pynq_demo`'s parameters -- `capture`,
`mode`, the receiver's capacity -- never an environment variable.

On macOS, Vivado runs in a container; point the container wrapper at the
directory to build in and give it this script as the command.

## Timing margin, and what your build will and will not match

The design closes, but not with room to spare. Measured here on
xc7z020-1 with Vivado 2025.2: **WNS +0.003 to +0.020 ns** at 148.5 MHz
across the last few builds. A different Vivado, a different speed grade
or a different seed may miss, and the lever that matters is placement --
`place_design -directive ExtraTimingOpt` in `impl_a.tcl`. On one and the
same design, `Explore` gave +0.001 and `ExtraTimingOpt` gave +0.055; the
post-route physical-optimisation loop can only hand back what placement
left on the table.

Your bitstream will also not be byte-identical to the one in the demo
video. The depth model has moved since that recording, so one block cuts
into three pipeline stages where the filmed build used four. Both are
twin-verified bit-exact against the same NumPy models and both close --
the pixels are the same, the schedule is not.

## After the build: the grabber's write offset

One number may need measuring, and it is measured PER LOCK -- not per
bitstream. The grabber's write engine lands each frame at an offset
decided when the link comes up (values seen on one unchanged bitstream:
0, 16, 48, 80). Reloading the bitstream re-rolls it; so does the source
disappearing and returning. The display is direct and never carries it.

That has a practical consequence: do not bisect it by reloading,
because every reload measures a different system. Measure once, and
compensate where the frames are consumed -- a grabbed frame carries
the offset; the reader that judges it corrects for it.

Measure it with the grabber and a known pattern: stream one of the
protocol's test patterns from the source (`bench/mkpattern.py`
authors one AT YOUR PIPELINE'S OWN DEPTH, which is the depth in
the ISP composite's input port and nowhere else; a pattern authored at any other
depth is refused in a way that looks like a geometry fault and is not), `bench/grab.py` a frame, and read the
roll off the known landmarks in the file. The display is direct and
never carries the offset; only grabbed frames do, and the reader
that judges them rolls it back. The compensation disappears
entirely when a fabric write engine replaces the vendor one.

Any Vivado from 2025.2 works, containerized included (if yours crashes
after "Routing Is Done", see the ledger: it is not your design).

## The loopback

The display side closes the loop on one board: np2hw's scanout — a
raster generator emitted from the one mode table, the header-parsing
receiver's counterpart — drives Digilent's rgb2dvi on HDMI OUT, fed
directly from the ISP stream. It arrives with the claims this bench
paid for: a frame may be dropped but never displaced, a window that
does not fit is refused rather than clipped, and pixel and enable
leave together. The picture is genlocked — the display clock follows
the sensor's rate through the MMCM's fine phase shifter while the
raster's geometry never moves — so latency to glass is lines, always:
there is no frame store in the picture path. Memory is an
*instrument*: a grabber captures frames to an address software chose,
for software to judge, and the display never depends on it.
Nothing runs the loop: power-on defaults enable the picture, so a
board with the bitstream loaded IS the camera's display -- software
is a set of visitors (`bench/load.py` once at boot,
`bench/status.py` when curious, `bench/grab.py` to capture,
`bench/calibrate.py` to write values by hand; a sensor's profile
otherwise arrives over the cable, from the source's packages).
`clocking.md` tells the board's clocks-and-rates story: the cable in,
and its two crossings.

The ARM block was a placeholder, and it has been replaced:
`isp.json` describes a revela pipeline -- the standard ISP,
`bayer_isp` (black level, white balance, Hamilton-Adams demosaic,
colour matrix), then a display curve and the mark -- and
revela generates it through np2hw, depth-checks every stage against
the clock (`--clock-mhz`; a too-deep stage is CUT into pipeline stages
by the traced depth model, and the one refusal left -- a single
operation deeper than the clock -- names itself), proves the
composition bit-exact against its own NumPy models under Verilator,
and only then emits Verilog. The whole ISP closes timing at the link's
148.5 MHz on the board's own clock. In the fabric it sits between
the receiver and the output tee; a control bit selects the stream's
consumer (the judge's capture path, or the ISP), and the ARM has no
standing job at all: pixels do not touch software, and neither does
the picture's survival. The camera is on the TV, in colour.

## Values: the design's at reset, the sensor's from the Pi

Two owners, two routes:

| values | owner | how they arrive |
|---|---|---|
| the 2.2 display curve (`rgb_gamma` in `isp.json`) | this design: the same for every sensor here | the bitstream's reset values -- a freshly loaded board already shows a gamma-correct picture |
| pedestal, balance, matrix (`revela.sensors.ov5647.profile:ov5647`) | the sensor, a profile for `bayer_isp` | written by the Pi over the cable at every link-up |

The register map records where each composite sits (`isp` is the
`bayer_isp`), so the sensor's profile resolves to this build's absolute
keys. Build the Pi's two packages as a pair, since the ISP package bakes
this build's map:

```sh
python3 -m revela.host.bridge_module --regmap build/pynq_z2/hdl/revela_isp_core_regmap.json \
    --out revela-isp.tar.gz
python3 -m revela.host.sensor_module --sensor ov5647 --out sensor-ov5647.tar.gz
```

At link-up the ISP package checks the far end runs this build, then
writes the sensor package's values at every `bayer_isp`. Without a Pi,
`bench/calibrate.py` writes the same resolved values from the board
itself (see its docstring).

## Porting to another board

Most of this design does not know what board it is on. The generated
sources take their shape from arguments, not from a part number, and
the ISP and the raster are proved against their models before they are
emitted. A port is a new package beside this one:

| what | where | how you get it |
|---|---|---|
| the jacks | `hdmi_in` / `hdmi_out` composites: their cells, pins and constraints | your board's master XDC; the front end from bayerlink-fpga, whose lane map is SOLVED with the protocol's test patterns, never guessed |
| clocks, PS settings | the design's `ps7` and clock-wizard nodes, on the `zynq7` family library | your board's preset or the vendor's own file |
| the pixel clock | the link, not a choice | it is whatever your source sends |
| the write-side skew | measured per link lock | the grabbed-pattern recipe, above |

What you do NOT port: the receiver, the ISP and the raster themselves.
They are generated for your width, depth and video mode, and they are
timed by np2hw's traced depth model, which cuts each pipeline for the
clock you name. A different speed grade shifts that model, not the
design -- `np2hw.timing.DEVICES` is where a device's constants live,
and adding one is a table entry.

## The bring-up ledger

Lessons this repo already paid for, so you do not have to:

- A floating ENABLE is a disable; a floating ARESETN is a permanent
  reset. Block designs tie silence to zero — audit every control input.

- Never `catch` a connect. A swallowed wiring error costs a bench
  iteration; a loud one costs two minutes.

- Design automation can leave a DMA's interconnect master unconnected
  and call it a warning. Wire one interconnect explicitly; pin address
  segments explicitly.

- Vivado in containers: the post-route abort is webtalk's libudev
  enumeration corrupting the heap. Stub `libudev.so.1` (empty answers)
  and it never happens again.

- At 148.5 MHz, memories decide timing: a combinational read of a
  deep FIFO is a thousand-LUT mux (register the read, let it be block
  RAM), and a wide distributed bank's write fan-out is a net you can
  see from orbit (keep banks shallow, stage the write).

- A clocking wizard fed from FCLK needs `PRIM_SOURCE No_buffer`; the
  default builds an input path to a package pin that is not there,
  and the MMCM never sees an edge. Perfectly silent.

- The transmit recipe that lights displays: your own MMCM making the
  exact pixel clock, rgb2dvi generating its serial clock from it
  (MMCM, range 2), held in reset by nothing but the pixel MMCM's own
  lock. Clock quality IS the product: "TPG started!" with a black
  screen is an undecodable TMDS spectrum, not a missing enable.

- An IP block whose lock you cannot explain is replaceable. The
  timing controller pair here never locked under any documented
  configuration; a raster of our own is 90 lines, and every decision
  it makes is a status bit.

- A DMA writer must not outlive its buffer. A freed framebuffer is
  the kernel's to hand to anyone, and a VDMA still writing it sixty
  times a second corrupts page cache and, through writeback, the SD
  card underneath. Two "dying" cards were this one bug. Halt the
  write channel in every exit path; the read side may keep the
  picture -- reads hurt nobody.

- A warm reboot does not clear the PL. Halt fabric DMA writers
  BEFORE rebooting (one devmem poke), or the new kernel boots under
  fire from the old design.

- A clock island is a workaround, not an architecture. With the
  compiler placing pipeline registers from its traced depth model,
  the ISP rides the receiver's clock and the island retires.

- The last picoseconds belong to the tools: escalate post-route
  phys_opt, then reseed placement (Explore). An RTL fix below a
  hundred picoseconds of WNS is chasing placement noise.

- A raster must not start a frame on faith. If the read engine has
  not delivered that frame's first beat when active video begins,
  starting anyway paints the WHOLE frame displaced by the gap -- and
  no audit downstream can see it, because a beat that has not
  arrived carries no evidence. Arm per frame: a frame may be
  dropped, never displaced.

- A read engine whose start-of-frame marker travels ahead of its
  data cannot be compensated, only retired: the offset first looked
  constant per bitstream (306, 322, 338 here), then turned out to
  re-roll on every link lock. The reader that replaced it owns its
  addressing, so its first beat IS the frame's first pixel.

- When a picture is in the wrong place, bisect before theorising: a
  raster that paints from its OWN counters, ignoring the stream,
  separates "my timing is wrong" from "the data arrives wrong" in
  one build. Hours of hardware guessing did not.

- A reader must not outlive its buffer either. It corrupts nothing,
  but it faithfully displays whatever the kernel puts in those
  pages, which looks exactly like broken hardware.
