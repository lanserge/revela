# Pipeline descriptions: what the primitives are

DRAFT research, 2026-09-26. Not a decision and not a patch list: an attempt to
derive, from what a camera pipeline actually IS, the smallest set of primitives
a description needs, how they are written, and how they connect -- then to test
that set against every pipeline we know we must express.

The current schema is not the starting point. It is one of the things the
result must be able to express.

---

## 1. Method

1. Collect the pipelines that must be expressible (section 2) -- real ones,
   each already hit in this project or clearly on its path.
2. Find the model underneath them (section 3), drawing on formats and
   languages that already solved parts of the problem (section 4).
3. Derive the primitives from the model, not from the use cases one by one
   (section 5), and decide how each is written and connected (sections 6-7).
4. Express EVERY use case with those primitives (section 8). A use case that
   needs a special case means a primitive is wrong or missing.
5. Only then write a schema, the examples first.

## 2. The use cases

| | pipeline | what it demands |
| --- | --- | --- |
| U1 | Bayer ISP chain: black level, balance, demosaic, matrix, curve | pixel operators in a chain |
| U2 | statistics tap | an observer: consumes a stream, produces values software reads |
| U3 | preview at lower resolution beside full resolution | a fork; streams of DIFFERENT geometry |
| U4 | stereo | two inputs, one chain described once, instanced twice, per-eye facts |
| U5 | luma/chroma split, sharpen luma only, merge | split and join; branches of different latency |
| U6 | time-of-flight | a capture loop of 4 phases; each result from the last 4 frames; per-frame meaning |
| U7 | frame-sequential HDR | a loop of exposures; per-frame exposure values set by AE, applied frames later |
| U8 | line-interleaved (DOL) HDR | two exposures from one sensor as two streams, rows apart; a ROW delay aligns them |
| U9 | recursive temporal noise reduction | the output, one frame late, is an input: a feedback loop |
| U10 | the whole camera path | receiver with header facts and its own clock -> ISP -> display raster or memory |
| U11 | decimated output | only every 4th frame leaves: a rate change |
| U12 | AE / AWB | software reads statistics of frame N and writes registers for frame N+k |
| U13 | one bitstream, many sensors | CFA order, geometry as run-time facts, not build options |
| U14 | variants | the same block at another fixed-point format, CORDIC depth, table size |
| U15 | shared vs per-instance tuning | stereo eyes sharing a tuning, one eye corrected |

## 3. The model underneath

Every use case above is a **synchronous dataflow program running at two nested
rates**: pixels within a frame, streamed in raster order, and frames within
time. Two observations make that precise.

**There are two kinds of signal, not one.** A *pixel stream* carries a raster
per frame. A *frame value* carries ONE value per frame, constant for the whole
frame: a register, the Bayer phase, a frame's loop position, an exposure, a
statistic. Today these are four unrelated mechanisms -- block registers,
pipeline context, header facts, statistics outputs -- but they are the same
thing: a value that changes, if at all, at a frame boundary. They differ only
in WHERE the value comes from:

    written by software        a register (commits at a frame boundary)
    carried by the stream      a header fact (width, CFA phase, frame counter)
    computed by the pipeline   a statistic, a frame's loop position
    looked up by loop position a table of per-position values (a phase, an exposure)

**There is one temporal operator: delay.** "The value k frames ago" covers
every temporal thing in the list:

    U6 ToF         the last four frames = the stream delayed by 0, 1, 2, 3
    U7 HDR         the exposure of frame n-k = the exposure value delayed by k
    U9 NR          the previous output = the output delayed by 1, fed back
    U8 DOL         the long exposure aligned to the short = a delay of r ROWS

A delay applies to both kinds of signal: on a pixel stream it is memory (a
frame store, or line buffers for rows); on a frame value it is a few
registers. And delay is what makes a feedback loop legal: a cycle is
schedulable if and only if a delay sits on it -- the rule every synchronous
dataflow system uses, and exactly what U9 needs.

**Rate changes are sampling.** U11 (every 4th frame) is a stream present on a
subset of frames. The capture loop of U6/U7 is a periodic clock of period N
frames; "frames where the loop position is 3" is a sampled clock.

**Space is not in the schema.** Stencils, crops, scaling are what a block's
NumPy model does, traced by np2hw. The description never sees a line buffer.
Geometry is a property of each stream, traced -- declared only where a stream
enters from outside.

## 4. What existing formats and languages teach

- **Lustre / SCADE, Signal** (synchronous languages used for safety-critical
  embedded control). Programs are equations over streams; `pre x` is the
  previous value of `x`, `x fby y` "x followed by y"; `when` samples a stream
  on a clock, `current` holds it. Cycles must pass through `pre`. This is the
  model of section 3 almost verbatim, proven over decades on hardware-timed
  systems -- delay and sampling as the ONLY temporal primitives.
- **Synchronous Dataflow** (Lee and Messerschmitt). Actors with fixed rates;
  a DELAY is an attribute of an EDGE (initial tokens), and a cycle is legal
  only with enough delay on it. Suggests delay could live on a connection
  rather than as a node.
- **OpenVX** (Khronos vision graphs). A *delay object* of N slots, read by
  index (0 current, -1, -2 previous), aged once per graph execution: the frame
  history, standardised for vision hardware.
- **Simulink**. Signals have sample times; a *rate transition* block is where
  rates meet; parameters are resolved through a workspace hierarchy by name.
  Precedent for rate as a signal property and for scoped parameter lookup.
- **Halide**. Separates the ALGORITHM (what is computed) from the SCHEDULE
  (where and how). np2hw already follows the idea; a pipeline description
  should separate structure from placement the same way.
- **IP-XACT** (np2hw exports it). *Design* (instances, interconnections) is
  separate from *design configuration* (values per instance, addressed by
  path) -- the description / profile split, with path addressing.
- **ONNX**. One node schema with kind-specific attributes; subgraphs as node
  attributes. Shows how many kinds share one node shape cleanly.
- **GStreamer**. A `tee` needs a `queue` per branch -- the fork-buffering law
  np2hw enforces, surfaced to the author.
- **Node-RED, React Flow, LiteGraph** (GUI node editors). Layout lives in the
  same document as the graph, per node -- one file, never two.

## 5. The primitives

Derived from section 3; each earns its place by a use case nothing else
covers.

| primitive | what it is | needed by |
| --- | --- | --- |
| **signal** | a named value flowing between nodes, of one of two RATES: pixel stream or frame value. Its word (channels, bits) and geometry are traced, not declared | everything |
| **node** | an instance of an operator: a block from the catalog, or a structural operator np2hw generates. Has pixel ports and value ports | everything |
| **delay** | a signal k frames (or r rows) back. On pixels: memory. On values: registers. The only temporal primitive; makes cycles legal | U6 U7 U8 U9 |
| **loop** | a periodic frame clock of length N, defined by the sensor; gives each frame a position | U6 U7 |
| **sampling** | a signal present only on some frames (by loop position, or every m-th) | U11, gating in U6 |
| **variable** | a frame-value source with an ORIGIN (software, stream, pipeline, loop table) and an ACCESS (write, read). Registers, context, header facts, statistics and per-frame tables are all variables | U2 U7 U12 U13 U15 |
| **composite** | a named subgraph with an interface (ports, exposed variables), instanced by path | U4, reuse |
| **boundary** | where a signal enters or leaves: declares what only the outside world knows (bit depth, channels, the facts its header carries, its clock) | U4 U10 |

Four things that are deliberately NOT primitives:

- **line buffers, windows, crops** -- inside blocks, traced (section 3).
- **fork** -- a signal connected to several consumers; buffering is the
  composer's job, per np2hw's law.
- **the history node** -- sugar: a window is several delays of one signal.
- **addresses** -- allocated, never declared.

## 6. How they are written: four layers, one tree

A description is ONE tree of named entities, and every entity has a PATH
(`left.blacklevel.offset`, `tof.hist`, `hdr.exposure[2]`). The same path names
it in the description, in a profile, in the register map, in the host API and
in the GUI. What varies by concern is which LAYER a fact belongs to:

| layer | says | changes when | file |
| --- | --- | --- | --- |
| **structure** | nodes, signals, delays, loops, variables that exist, composites, build parameters (generics) | the hardware changes | the description |
| **configuration** | the VALUES of variables | tuning changes | a profile |
| **target** | where memory lives, clock rates, device, bindings | the board changes | a target file |
| **presentation** | node positions, groups, notes | someone rearranges the drawing | the description, in a `ui` section the builder ignores |

Build parameters (a history's frame count, a CORDIC's depth, a fixed-point
format) belong to STRUCTURE: they change the hardware, as VHDL generics do.
Values belong to CONFIGURATION: they change what the hardware holds.

## 7. How they connect

Three kinds of link, and nothing else:

1. **Edges** join an output port to input ports: `a.out -> b.in`. Pixel and
   value signals both flow on edges; a delay is written on the edge that
   carries it (`hist.in -> quad.in @ -1`), or as a delay node -- to decide
   (section 9).
2. **Ownership**: a node's own variables (its registers) belong to it by
   path, with no wire. A GUI that drew a wire for every register would be
   unreadable; a block's coefficients are simply part of the block.
3. **References** name a shared entity by path where a wire would be wrong:
   a pipeline-wide variable a block consumes (`bayer_phase`, as `consumes`
   does today), the loop a table is indexed by, a store read far from where
   it is written.

Legality, checked at load: every input driven exactly once; every cycle
carries a delay; a fork buffered as np2hw requires; a reference resolves; a
variable's origin matches how it is used (nothing writes a stream-carried
fact; nothing reads a pixel-rate signal as a frame value).

## 8. Every use case, in these primitives

| | expressed as |
| --- | --- |
| U1 | nodes and edges |
| U2 | a node with a pixel input and value OUTPUTS (variables, access read) |
| U3 | one signal to two consumers; the scaler's output geometry is traced |
| U4 | two boundaries with their own facts; one composite instanced twice |
| U5 | split and join as nodes; the composer delay-matches the branches |
| U6 | boundary -> delays 0..3 gathered into one input of `tofquad`; per-frame weights or phases as a loop-indexed variable; optional sampling for coherent output |
| U7 | as U6, the per-frame exposure a loop-indexed variable written by AE, read delayed by k for frame n-k |
| U8 | two boundaries (virtual channels) from one receiver; a ROW delay on the long exposure |
| U9 | NR's output edge fed back to its own input through a delay of 1 frame -- a legal cycle |
| U10 | receiver and transmitter as structural nodes; clock domains on boundaries; a crossing where domains meet |
| U11 | sampling by loop position or by m |
| U12 | statistics as read variables; registers as write variables; `frame_id` as a stream-origin variable for attribution |
| U13 | CFA order and geometry as variables whose origin is the stream or software, never a build option |
| U14 | build parameters on the node (generics), validated against the block's declaration |
| U15 | a profile's values keyed by composite path (shared) or instance path (one eye) |

Nothing needed a special case. If that survives drafting the examples in
JSON, the primitive set is right.

## 9. What must be decided

1. **Delay on the edge or as a node?** On the edge is the SDF form, and makes
   U9's cycle read exactly as it is. As a node it has somewhere to carry
   attributes (memory binding, frame count). Possibly both: an edge attribute
   the loader lowers to a structural node.
2. **Value signals as edges, or only by reference?** Wiring a statistic to a
   consumer is natural; wiring every register is not. Proposed: ownership for
   a node's own variables, references for pipeline-wide ones, edges only when
   a value is routed from one node to another.
3. **Scope.** The ISP core only (as today), or the whole camera path from the
   receiver (U10)? The model covers both; the second replaces hand-written
   board glue and is what a GUI user expects.
4. **Where the loop is declared.** The sensor defines its capture loop; the
   description references it -- or states it itself.
5. **One values format.** A profile tree mirroring the description's paths,
   arrays as arrays -- replacing today's two shapes.

## 10. Next

1. Settle section 9.
2. Draft every use case in section 2 as a JSON description with these
   primitives, BEFORE any schema is written -- the examples are the
   specification, and the ones that read badly show where the primitives are
   wrong.
3. Write the schema from the examples; then the loader; then make each
   example build and run under `revela run --rtl`.
