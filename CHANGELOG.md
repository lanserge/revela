# Changelog

Notable changes to revela. Two things here need naming loudly whenever
they move, because software downstream reads them: the **register map**
that a host uses to find a block's coefficients, and the **boundary
widths** an integrator wires to.

Pre-1.0, so the block API and the map format can still change. See
`docs/RELEASING.md` for which version numbers must be bumped when.

## 0.3.0

### Added

- **`sync_memory`, threaded to where a pack is built.** np2hw 0.6.0 puts
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
