# experiments

Exploratory work. **Not part of the library.**

Two rules, and they are the whole point of this directory existing:

1. **Float is fine here.** Rule 1 — one model per block, at the hardware's
   arithmetic, no float — governs `revela/`. It does not govern this directory.
   Comparing a candidate algorithm against a floating-point ideal, or against
   another candidate, is exactly the kind of question that belongs here.

2. **Nothing in `revela/` may ever import from `experiments/`.** Not a helper,
   not a constant, not a test fixture. If something here turns out to be needed
   by the library, it gets rewritten there at the hardware's arithmetic, with a
   bit-exact test — it does not get imported.

`pytest` does not collect this directory (see `norecursedirs` in
`pyproject.toml`), and nothing here is packaged in the wheel.

## What belongs here

- Comparing demosaic algorithms — bilinear against Malvar against Menon — on
  real images, which is the only way to choose between them and is explicitly
  NOT something the per-block bit-exact tests can tell you.
- Deciding how many bits a datapath actually needs, before committing it to a
  model.
- Tone curve and CCM exploration against reference images.
- Checking whether an approximation is good enough to be worth its area, which
  necessarily means measuring against something more exact than itself.

## What does not

Anything that has become a decision. Once an algorithm is chosen, it moves to
`revela/blocks/` as an integer model written at the hardware's arithmetic, and
the exploration that led to it stays here as the record of why.
