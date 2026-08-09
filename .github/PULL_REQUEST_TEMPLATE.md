## What this changes

<!-- One or two sentences. If it fixes an issue, link it. -->

## Contributor Licence Agreement

- [ ] I have read [CLA.md](../CLA.md) and agree to it for this and my future
      contributions.

It is a **licence grant, not an assignment — you keep your copyright.** One
signature covers everything you contribute afterwards. If you cannot sign,
say so here rather than closing the pull request: a bug report with a failing
test case is valuable and needs no CLA at all.

## Checks

- [ ] `pytest` passes locally (or I have said which tests I could not run).
- [ ] New `.py` files under `revela/` start with the copyright and
      `SPDX-License-Identifier: Apache-2.0 WITH SHL-2.1` header.
- [ ] No new dependency. <!-- If there is one, name it and its licence. Only
      permissive licences can be accepted; see docs/THIRD-PARTY.md -->

## If this adds or changes a block

- [ ] The NumPy model is written at the hardware's arithmetic — no float
      reference model, no float-versus-fixed comparison.
- [ ] A test proves the generated Verilog is **bit-exact** with the model under
      randomised backpressure, framing flags included.
- [ ] Every `Param` has a `description`, written for somebody reading the
      generated RTL who has never seen the Python.

## If this adds or changes a sensor description

- [ ] Values are derived from the datasheet. **No register table is transcribed
      from a Linux kernel media driver** — their licence is incompatible with
      this project's. See CONTRIBUTING.md.
- [ ] `provenance` states the derivation, and no per-unit calibration data is
      included.

<!--
The four rules are in docs/design-rules.md and are the design, not preferences.
A pull request that breaks one will be asked to change regardless of how well it
works.
-->
