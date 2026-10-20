# Changelog

Notable changes to revela. Two things here need naming loudly whenever
they move, because software downstream reads them: the **register map**
that a host uses to find a block's coefficients, and the **boundary
widths** an integrator wires to.

Pre-1.0, so the block API and the map format can still change. See
`docs/RELEASING.md` for which version numbers must be bumped when.

## Unreleased

### Changed

- **The frame count rides with the stream.** `frame_seq`, the count each
  bayerlink header carries, is a stream context fact, latched by
  `isp_entry` with the frame's geometry; a block following a capture loop
  binds its place in it to an expression over the count (`"frame_seq &
  3"`). It replaces `frame_phase`, which nothing had yet read.
- **A new raster starts dark, for long enough that a TV trains on it.** A
  TV trains its receiver to a bit rate the first time it meets it and
  keeps the training; at 1.485 Gb/s, past the rated serializer of the
  PYNQ-Z2's -1 part, a test chart left one searching for minutes after a
  switch from 1080p30, where a dark picture let it find the same raster at
  once. So for 4 s after the raster starts -- power-up, reset, a clock
  switch -- it paints the fill while the stream is taken and the genlock
  runs beneath (`raster.v`'s `G_QUIET_STEPS`, counted in fine phase steps
  so it lasts as long at any clock). A new picture on the same raster
  starts nothing and shows at once.
- **The genlock steers smoothly.** Each frame start's error feeds a loop --
  an eighth of the error a frame, and an integral that learns the two
  crystals' difference -- and the steps it asks for are spread evenly
  across the next frame, so the display's clock is a few ppm off nominal
  and constant, never pulsed at the frame rate. From a jam the error peaks
  near 200 pixels and settles in about two seconds.
- **Every frame on the cable is whole.** The raster's counters never
  jump. A raster starts aligned: from power-up, reset or a clock switch it
  holds a blank line until a frame start arrives, then as many lines more
  as put the next frame start on the target, wherever the window sits. An
  error past two lines finishes the frame on the cable and holds at its
  end for the next frame start -- one long vertical blanking where the
  counters used to jam, cutting a line and a frame short. A frame is shown
  only from its own first pixel and only while the genlock is locked,
  decided at that pixel: a frame start mid-window, or a stream whose frames
  do not fit the raster, shows nothing but the fill.
- **The display clock switches between frames, with its mode.** The
  clock's row is one of the raster's armed numbers, so the clock select
  switches as the raster takes them, after a frame's last line; the
  firmware stages the serializer, arms, waits for the copy, then loads the
  serializer on the new clock. The clock select's own select pins are not
  timed (`raster-clocks.xdc` says why).

- **The PYNQ-Z2 receives HDMI with bayerlink-fpga's own receiver.** The
  board's `front_end` chooses the HDMI input's implementation, `tmds_rx` by
  default: it aligns in a fraction of a second where Digilent's dvi2rgb
  took seconds on every mode change, and its account of the link -- the
  bond, the clock, each lane's alignment, eye and bit slip, and its survey
  of every lane's delay taps -- is on a GPIO of its own, `rx_gpio`, every
  field named; `bench/rx_survey.py` draws each lane's eyes. The board
  test holds the receiver's VCO and lane order to whichever implementation
  is chosen.
- **A GPIO field may be equal parts.** A field with a `count` is that
  many parts, part 0 lowest -- a value per lane in one port -- and the
  bench reads and writes it as a list.
- **The firmware leaves a running display clock alone.** A configuration
  whose clock the hardware already runs -- its select on that row, the
  serializer's MMCM locked on that row's recipe, as they read back -- is
  applied without reloading the serializer, which stops the HDMI clock and
  makes a TV find the signal again. The firmware's status reports the
  display's counters (`counters`), the longest raster it scans.

- **The picture is cropped in the ISP, and the display is sized by the
  stream.** `revela.blocks.crop` is NumPy's own slice on four registers,
  between the display curve and the mark; at reset it keeps the whole
  frame. The firmware sets it to the middle of the frame, as much as the
  display shows, and places the mark in the corner of what it keeps. The
  ISP publishes the size of the frame it hands on, which crosses to the
  display with the picture (np2hw's `frame_cdc`); the raster takes it in
  vertical blanking, and the display's `win_w`/`win_h` registers are
  gone.
- **The ISP's clock is published.** The board's cable map states the
  clock the ISP takes a sample a cycle on, traced from the fabric clock
  driving it, and the firmware reports it (`isp_khz` in `status`), so a
  source times its lines to what the ISP drains. With no sensor -- a clip
  playing -- the Pi platform offers the firmware no sensor functions, and
  a frame of any size is taken without touching one.
- **The PYNQ-Z2 receives a 148.5 MHz link.** `link_mode` is 1080p60, so
  the HDMI input's front end is built for the 120-240 MHz span with its
  VCO in specification; the board test holds that property rather than a
  rate.

- **The firmware** (`revela/firmware`): portable C that will run on the
  MCU beside an ISP, and runs on a Pi beside the board now. A platform
  lends it a HAL (`revela_hal.h`: the ISP's registers, the sensor by what
  it is asked, time, log); it offers actions (`revela_fw.h`: init and
  configure at a configuration it is given -- frame, Bayer phase,
  display raster, window, 3A settings -- a per-frame tick, named
  parameters, status, and the same as a text console). It keeps no
  table of modes: the caller passes numbers. The core is fixed C beside
  a generated `core_map.h`; AE, AWB and AF are each their own module
  (stubs until the statistics arrive); a sensor module is its facts and
  profile as data. `revela.firmware.package` cuts each as a picam2hdmi
  module, and `platform/pi` is the Pi's half. This replaces
  `revela.host.bridge_module` and `revela.host.sensor_module`.

- **The PYNQ-Z2 raster is fixed Verilog, its mode a register.**
  `boards/common/hdl/raster.v` (behind `scanout.v`) replaces np2hw's
  generated scanout: the same raster and genlock, with its mode -- totals,
  syncs, polarities, the fine steps a pixel of its clock is -- and its
  placement arriving on a register file, armed across the clock gap and
  taken in vertical blanking. The file resets armed to the build's mode,
  so an unwritten bitstream shows what it did. Its crossing is np2hw's
  `stream_cdc`. `scanout.py` generates only the register file.
- **The cable reaches the raster's registers.** The DDC slave's bus is
  decoded: the ISP at 0, as before, and the raster's file beside it, off
  the processor's bus. The build writes `cable_map.json` beside
  `address_map.json`: the cable's space, the raster's fields and the word
  that identifies their layout.
- **The rasters are bayerlink's** (`bayerlink.rasters`), read through
  `revela.boards.common.clocking:pixel_khz` in a design's expressions.

- **The block framework moved to np2hw.** `revela.params` and
  `revela.stream` are `np2hw.blocks.params` and `np2hw.blocks.stream`;
  `Block`, `StreamPort`, `ContextBit`, `Generated` and the decorator are
  `np2hw.blocks`. `revela.blocks.ispblock` is np2hw's decorator with
  revela's pipeline context bound (`revela.blocks.context`, split out of
  `pipe`), and `registry()`/`resolve()` discover revela's blocks as before.
  The names revela re-exported at its top level are gone: revela is the
  blocks, pipelines and boards. Generated block Verilog says it was
  generated by np2hw, under the licence the model's source states.
- **Designs and pipelines moved to np2hw.** The composite format, its
  loader, the netlist, the wiring check and profile values are
  `np2hw.design`; composition, allocation, the register map and the RTL
  twin are `np2hw.pipeline` (`revela.designs.load` is
  `np2hw.pipeline.load`). revela's pipe block is named to np2hw through
  revela's context `Vocabulary` (`revela.blocks.pipe.VOCABULARY`), which
  `ispblock` binds; context signals that follow the frame's geometry and
  depth say so (`follows`). The register map's `generator` is `np2hw`.
- **The Vivado flow moved to np2hw.** `revela.boards.vivado`, `bdtrace`,
  `vendor_check` and `checkhwh` are `np2hw.toolchain.vivado` and its
  modules; a board's build calls them from there. `revela.boards.modes`
  is gone: a design reads a raster's pixel clock as
  `@np2hw.video_out:pixel_khz(mode)`.
- **The commands moved to np2hw.** `revela run` and `revela generate` are
  `np2hw pipeline run` and `np2hw pipeline generate`; `revela.fusesoc.emit`
  is `np2hw.pipeline.pack.emit`, and `revela.core`'s generator runs
  `np2hw-fusesoc`. The `revela` and `revela-fusesoc` scripts and
  `python -m revela` are gone. `--bayer-phase N` is
  `--context bayer_phase=N`.
- **The tone curves state their output depth once.** `gamma` and
  `rgb_gamma` no longer declare `out_depth` beside their models: the depth
  they hand on is traced, and np2hw's model run reads it from the trace.
- **The board's register bus is the design's.** Vivado's automation and
  `bus.tcl`, which repaired what it built, are gone. `pynq_demo` wires
  its bus explicitly: the processor's port onto np2hw's AXI3 bridge, a
  decoder onto every register slave at the window each states, an arbiter
  in `bayerlink_display` sharing the ISP's registers with the DDC bridge,
  and the DMAs onto memory through an explicitly configured interconnect.
  np2hw's reset synchroniser replaces both `proc_sys_reset` cells. Every
  register slave is on the processor's clock: the ISP's and the raster's
  register files cross to it themselves, so the island-clock bus and its
  clock converters are gone.
- **The host finds registers in `address_map.json`**, written beside the
  bitstream; the hardware handoff no longer lists them. The bench scripts
  read it through `bench/addresses.py`.
- **`stream_cdc` is np2hw's description** (`np2hw.hdl.stream:stream_cdc`):
  an np2hw module's ports and constraints are stated beside it, not here.
- **The board's glue is np2hw's**: constants, slices, concatenations and
  inverters are np2hw's portable modules (`np2hw.hdl.glue`,
  `np2hw.glue:concat`) instead of Xilinx's cells; the interrupt
  concatenation says it carries interrupts, which is how the processor's
  interrupt input is sized.
- **A GPIO is a described block** (`revela.boards.common.gpio`, filled by
  AMD's AXI GPIO), held to its description by `vendor_check` like the
  DMAs, its register port stating its window.

### Fixed

- **A verified pack's manifest named the pipeline's input.** The emitter's
  loop over the inputs reused the variable holding the VLNV, so a pack
  built with the twin on (the default, and the generator's) wrote a core
  called after its last input and named the file `generated.core`.

## 0.3.1

### Fixed

- **The np2hw floor names a version that exists.** 0.3.0 declared
  `np2hw>=0.6.0`, and np2hw 0.6.0 was withdrawn from the index. While
  nothing at or above that bound was published, `pip install revela`
  could not complete. np2hw 0.6.1 clears the bound arithmetically — a
  floor is a lower bound, not a name — so what this release really
  changes is that the number written down is one a reader can go and
  fetch. No revela code changed.

## 0.3.0 (withdrawn)

Removed from PyPI. Its `np2hw>=0.6.0` floor pointed at a release that had
itself been withdrawn, and while nothing at or above that bound existed
this version could not be installed. np2hw 0.6.1 later cleared the bound,
but 0.3.0 had already been removed and a burned version does not come
back. **0.3.1 is this release with the floor corrected and no code
changes** — what follows describes what shipped there.

### Added

- **`sync_memory`, threaded to where a pack is built.** np2hw 0.6.1 puts
  every line buffer behind a module a memory macro can be bound to, and
  refuses the emitters whose line buffers read combinationally — no SRAM
  implements a combinational read. That ask now reaches through
  `Block.generate`, `Pipeline.generate`, `emit()`, the FuseSoC
  generator's parameters and **`revela generate --sync-memory`**, so a
  pack bound for silicon is built under the promise that no memory in it
  is unbindable.

  The promise is refusal-only. A pack generated with the flag is
  byte-identical to one generated without; what differs is that the
  second could have contained something no macro implements, and the
  first could not have been written.

- **`memories` in `<name>_build.json`.** Per stage: the instance, the
  seam module it sits behind (or null), width, depth, and whether the
  read is synchronous. An integrator binding macros starts from the
  build report, not from a grep of the Verilog — the same reason the
  boundary is published there.

### Changed

- **The np2hw floor is `>=0.6.0`**, because `Block.generate` passes
  `sync_memory` on every call and an older np2hw raises `TypeError` on
  the first block a pipeline builds. The previous floor said 0.5.1 and
  was wrong; CI caught it on a clean install, which is the only place it
  can be caught — a co-development checkout has np2hw installed editable
  and therefore always satisfies any floor.

The register map is untouched: `MAP_FORMAT_VERSION` stays 4, and a pack
built by this version is byte-identical to one built by 0.2.0 for a
design that does not ask for the flag.

## 0.2.0

### Added

- **The generated core publishes the boundary it traced.** `generate`
  emits `<name>_build.json` beside the Verilog: each output's
  `data_bits`, `channels` and `channel_bits`, the geometry the cores were
  built around, the latency, and the toplevel name.

  The ISP is the deliverable and whatever instantiates it is a harness,
  so the harness must not write the ISP's widths down — and until now it
  had to, because there was nothing to read. The input's depth is
  declared by the spec, but every output width is **traced**: a
  three-channel word is three fields wide because the models packed it
  that way, not because anyone chose the number. The total alone cannot
  be sliced, which is why the channel count travels with it.

  The geometry is there for a reason of its own: the cores get neither
  the stream's end-of-line nor the header's width, so a wider frame is
  not something they can notice. Whoever hands them one must crop first.

- **A register bank per block**, with each block's coefficients kept
  beside it, on the processor's clock rather than the pixel clock — a
  control interface may not depend on a clock that can stop, and one that
  does hangs the first software access after a cable is pulled.

- **Commit semantics, stated and tested.** Software says when a batch of
  coefficients is complete; the datapath takes the whole batch at a frame
  boundary. Poll for the arm to clear, because that is what software must
  do — and the map now tells the truth about it.

- **Two packages for the bridge**: the ISP policy package and the sensor
  driver, with the sensor's laws travelling beside its calibration. A
  sensor's gain law belongs to the sensor.

- **A design can declare that the stream carries its own geometry.**
  Context named in `stream.context` becomes an input port and gets no
  register; anything not named stays a writable `pipe` register. A fact
  is either a port or a register, never both — which is what makes two
  writers impossible to express rather than merely discouraged.

### Changed

- **Gamma narrows the datapath, and says so from its own declaration.**
  It is the last block that makes RGB and the point where linear light
  becomes display values, so it is where the width should drop: the wide
  signal the chain carries is headroom for black level, white balance,
  demosaic and the matrix, and it lands on the display's 8 bits *here*,
  chosen by the curve. Truncating later is a second, linear quantisation
  on top of the one the curve already made.

  **The output depth has one owner: the knot declaration**, now 9 bits
  wide, so gamma outputs 8. A deeper display is
  `{"knots": {"bits": 11}}` and nothing else states the number. A block
  that changes the width declares `out_depth`, and the model walk carries
  the answer forward.

  **Breaking for stored profiles.** Knots quantised against the old full
  scale are four times too large and will clip; the reference ov5647
  profile is rescaled in this release.

- **The version has one owner**, read back from the installed
  distribution rather than restated in `__init__`.

### Fixed

- The twin reads channels and lane width off the published boundary
  rather than dividing the word by the input depth, and refuses outright
  if the port and the report disagree. Dividing silently mis-unpacked any
  block that narrows.

### Internal

- The licence check reads the identifier from `pyproject.toml` instead of
  spelling it out in a regex — a test carrying its own copy of what it
  checks passes happily after a relicence, which is the one moment it was
  meant to speak up. It now also covers source that is not Python.
