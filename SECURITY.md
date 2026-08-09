# Security policy

## Reporting a vulnerability

**Email s.rabykin@gmail.com.** Put "revela security" in the subject.

Please do not open a public issue for anything you believe is exploitable, until
it has been fixed and released.

What helps: what you found, how to reproduce it, what an attacker gains, and the
version or commit you were on. A failing test case is worth ten paragraphs.

If you would rather report privately through GitHub, use
[Security → Report a vulnerability](https://github.com/lanserge/revela/security/advisories/new)
on this repository.

## What to expect

These are honest targets rather than a contractual SLA. A commercial licence includes a support agreement with actual
committed response times; see [FUNDING.md](FUNDING.md).

| | Target |
| --- | --- |
| Acknowledgement | 5 working days |
| Initial assessment | 15 working days |
| Fix or documented mitigation | Depends on severity; you will be told what the plan is |

You will be credited in the advisory unless you would rather not be.

## Supported versions

Pre-1.0: only the latest release on `main` is supported. There are no backports
to earlier versions yet.

## Scope

revela is a compiler and a hardware description library. It does not run as a
network service, so the realistic threat model is narrower than for most
projects. In scope:

- **Generated RTL that does not match its model** in a way an input can trigger.
  Bit-exactness is the project's central claim, and a silent divergence between
  the NumPy model and the emitted Verilog is treated as a security-class defect
  even though it is not a memory-safety bug — somebody is going to fabricate it.
- **Code execution while loading a description.** Design, profile and sensor
  JSON are data. If a crafted file causes code execution, a path traversal, or
  an unbounded allocation, that is a vulnerability.
- **Supply chain**: a compromised or typosquatted dependency, or a build that
  does not reproduce from the stated sources.

Out of scope:

- Vulnerabilities in Verilator, Icarus Verilog, or your synthesis toolchain —
  report those upstream.
- Image quality, timing closure, or resource usage. Those are bugs, and welcome
  as ordinary issues.
- Anything requiring an attacker who can already modify the repository or your
  local environment.

## Hardware findings

If you find that a generated design has a property with security consequences in
a product — a side channel, a way to make a block produce attacker-chosen output
from attacker-chosen input — please report it here rather than only publishing.
Somebody may already have taped it out.
