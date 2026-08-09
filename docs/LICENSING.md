# Licensing

What licence applies to what, in the order a commercial evaluator asks.

This is a factual summary, not legal advice, and not a substitute for reading
[LICENSE](../LICENSE). Where the two differ, the licence governs.

---

## 1. What licence applies to Verilog that revela generates?

**The same one: Apache-2.0 WITH SHL-2.1.** Every generated file carries an SPDX
header saying so.

A generated file has two origins, and only one of them puts terms on you:

- **The datapath is traced from the NumPy model** in `revela/blocks/`. That model
  is licensed under SHL-2.1, so what is derived from it carries those terms.
  This is not a claim revela makes about your work in general — it follows from
  the output being derived from licensed source, and the header records it rather
  than leaving it implicit.
- **The surrounding structure is emitted by np2hw** — the streaming handshake,
  the line buffers, the AXI4-Lite register file, the composed top level. In a
  typical design that is the majority of the file by line count. **np2hw grants
  its output unconditionally**, under the Output Exception in its licence: what
  the tool writes into your design is not covered by np2hw's licence, carries no
  attribution requirement, and may be used under any terms you choose.

So the terms on a generated file come from revela's models alone. np2hw
contributes code to the output and no obligations with it, which is the same
arrangement Bison and a compiler's runtime library use, and for the same reason.

## 1a. Does that mean np2hw's licence reaches my product?

No. Its Output Exception exists precisely so the question does not have to be
argued: you owe np2hw nothing for shipping what it generated, not even
attribution.

Note the exception grants additional permission and takes nothing away, so np2hw
is still MIT and still scans as MIT.

## 2. What do I owe you if I synthesise it and ship a product?

**Under the open licence: attribution, and nothing else.**

Concretely, when you distribute the work or a derivative — in source, as a
bitstream, or fabricated in silicon:

- keep the copyright notice, the licence text and the SPDX headers with the
  source you distribute;
- include the [NOTICE](../NOTICE) file's attribution content;
- state that you changed files, if you changed them.

Then:

- **No fee, no royalty, no per-unit reporting.**
- **No obligation to publish your product, your integration, your tuning, your
  calibration or your own blocks.** The licence does not require you to publish
  anything you change or build on top. Your modifications are yours to keep
  closed.
- **No obligation to open the bitstream or the mask work.** SHL-2.1 exists
  precisely to make that clear: it extends Apache's definitions so that "Object
  form" covers a bitstream, a netlist and a physical instantiation, and "Rights"
  covers design right and semiconductor topography rights, not just copyright.
  Apache 2.0 alone leaves this ambiguous for hardware, which is why it is not
  used here on its own.
- **No obligation to use the name.** The licence does not grant rights to it
  either — see [TRADEMARK.md](../TRADEMARK.md).

You may, at your option, treat any work released under SHL-2.1 as released under
Apache 2.0 instead. That option is in the licence.

## 3. What does the open licence give me on patents?

Apache 2.0 Section 3, inherited through SHL-2.1: each contributor grants you a
patent licence covering their own contributions, and it terminates if you bring
patent litigation alleging the work infringes.

That grant is **from the contributors, over their contributions**. It is not a
warranty that the work is clear of third-party patents, and no open licence
provides one.

## 4. What does the open licence NOT give me?

Stated plainly, because this is the part that decides whether you need a
commercial licence:

- **No warranty.** The work is provided "AS IS", without warranties or
  conditions of any kind. Apache 2.0 Section 7.
- **No indemnity.** If a third party asserts an IP claim against your product,
  you are on your own. Apache 2.0 Section 8 limits contributor liability.
- **No support commitment.** No response time, no named contact, no escalation
  path. Issues are answered when they are answered.
- **No fitness or conformance claim.** Bit-exactness between each NumPy model and
  its generated RTL is verified in CI and is the project's central technical
  claim. Whether the result meets timing in your process, at your clock, in your
  floorplan, and whether the image quality suits your product, is yours to
  establish.

None of that is unusual. It is what every permissive licence says, and it is
normally fine — until a procurement or legal process requires otherwise.

## 5. What does the commercial licence add?

It adds terms, not code. **The commercial tier is the same models and the same
generator; there is no separate fork with better arithmetic.**

| | Open (SHL-2.1) | Commercial |
| --- | --- | --- |
| Use, modify, ship, fabricate | Yes | Yes |
| Royalties | None | None |
| Keep your changes closed | Yes | Yes |
| Patent grant | Apache 2.0 §3, from contributors over their contributions | Express grant from the licensor, on contracted terms |
| Indemnity | None | Third-party IP indemnity, to an agreed cap |
| Warranty | "AS IS" | As agreed |
| Support | Best effort | Defined response time, named contact, escalation path |
| Advanced blocks | Not included | Included |
| Verification collateral | Not included | Constrained-random testbenches, coverage models, regression results, requirement-to-test traceability |

"Verification collateral" is usually the deciding item rather than the licence
terms: an external verification team asks for coverage and traceability, and an
open repository does not carry them.

The commercial licence does not restrict anything you already have. Code released
openly stays released; a later commercial tier cannot retract it.

## 6. Contributions

Contributors keep their copyright and grant a licence in — sublicensable and
transferable — which is what makes the dual offer possible. See
[CLA.md](../CLA.md), section 5 in particular, and
[CONTRIBUTING.md](../CONTRIBUTING.md).

## 7. Third-party components

Every dependency of revela is under a permissive licence, enforced in CI by an
allow-list. The simulators used for verification are invoked as separate
programs, which does not affect the designs they simulate. Full audit, including
the simulators' own terms: [THIRD-PARTY.md](THIRD-PARTY.md).

## 8. Who to ask

**s.rabykin@gmail.com.** Questions about a specific integration, a compliance
review, or commercial terms all go to the same place, and are answered by the
person who wrote the code.
