# revela Individual Contributor Licence Agreement

> **DRAFT — PENDING LEGAL REVIEW.**
> This document has been drafted by the project maintainer and has **not** been
> reviewed by a qualified lawyer. It is published so that the intended terms are
> visible and can be discussed before anyone signs. It may change. Do not rely on
> it as legal advice, and take your own advice before signing — particularly if
> you are contributing in the course of employment.

Thank you for your interest in revela (the "Project"), maintained by **Serge
Rabyking** ("the Maintainer").

This agreement is a **licence in**, not an assignment. **You keep the copyright
in everything you write.** What you grant is a broad licence to use it, including
the right to sublicense it to others on different terms.

Please read this document carefully before signing, and keep a copy for your
records.

---

## 1. Definitions

**"You"** (or **"Your"**) means the individual who owns the copyright in a
Contribution, or the legal entity authorised by that owner, that is entering into
this agreement with the Maintainer. For a legal entity, the entity making a
Contribution and all other entities that control, are controlled by, or are under
common control with that entity are considered a single Contributor. "Control"
means (i) the power, direct or indirect, to cause the direction or management of
such entity, whether by contract or otherwise, or (ii) ownership of fifty per
cent (50%) or more of the outstanding shares, or (iii) beneficial ownership of
such entity.

**"Contribution"** means any original work of authorship, including any
modifications or additions to an existing work, that is intentionally submitted
by You to the Maintainer for inclusion in, or documentation of, the Project. For
the purposes of this definition, "submitted" means any form of electronic, verbal
or written communication sent to the Maintainer or their representatives,
including but not limited to communication on electronic mailing lists, source
code control systems and issue tracking systems that are managed by, or on behalf
of, the Maintainer for the purpose of discussing and improving the Project, but
excluding communication that is conspicuously marked or otherwise designated in
writing by You as "Not a Contribution".

**"Successor"** means any legal entity that succeeds to the Project, including
without limitation a company incorporated by the Maintainer, a company into which
the Maintainer's sole trading business is transferred or reorganised, an assignee
of the Project, or an acquirer of all or substantially all of the assets of the
Maintainer's business relating to the Project.

## 2. You retain your copyright

You retain all right, title and interest in and to Your Contributions. Nothing in
this agreement transfers ownership of Your copyright, or of any other
intellectual property right, to the Maintainer. You remain free to use, license
and exploit Your Contributions for any purpose whatsoever, without restriction
and without any obligation to the Maintainer.

## 3. Grant of copyright licence

Subject to the terms and conditions of this agreement, You hereby grant to the
Maintainer, and to recipients of software distributed by the Maintainer, a
perpetual, worldwide, non-exclusive, no-charge, royalty-free, irrevocable,
**sublicensable and transferable** licence to reproduce, prepare derivative works
of, publicly display, publicly perform, sublicense and distribute Your
Contributions and such derivative works, in source or object form, and (where the
Contribution is or describes a hardware design) to make, have made and otherwise
instantiate that design in physical form.

For the avoidance of doubt, the licence granted in this section:

- includes the right to license and sublicense Your Contribution **under terms
  different from those under which You submitted it**, including under a
  proprietary or commercial licence, and to do so through multiple tiers of
  sublicensees; and
- may be **exercised, assigned and transferred** by the Maintainer to a Successor
  without any further consent from, or notice to, You.

## 4. Grant of patent licence

Subject to the terms and conditions of this agreement, You hereby grant to the
Maintainer, and to recipients of software distributed by the Maintainer, a
perpetual, worldwide, non-exclusive, no-charge, royalty-free, irrevocable
(except as stated in this section), **sublicensable and transferable** patent
licence to make, have made, use, offer to sell, sell, import and otherwise
transfer Your Contribution, where such licence applies only to those patent
claims licensable by You that are necessarily infringed by Your Contribution
alone or by combination of Your Contribution with the Project to which it was
submitted.

If any entity institutes patent litigation against You or any other entity
(including a cross-claim or counterclaim in a lawsuit) alleging that Your
Contribution, or the Project to which You contributed, constitutes direct or
contributory patent infringement, then any patent licences granted to that entity
under this agreement for that Contribution or Project shall terminate as of the
date such litigation is filed.

This licence is likewise transferable to a Successor, as set out in section 3.

## 5. Why the sublicense and transfer rights are asked for

Stated plainly, because it is the part of this agreement that matters most and
should not be buried:

The Project is released openly under the Solderpad Hardware License v2.1, and a
**commercial licence with support and indemnity is offered alongside it**. Selling
that commercial licence means licensing the whole work — including Your
Contribution — on terms other than the open ones. That requires the right to
sublicense.

The Maintainer is currently a sole trader and may later incorporate. Without an
express right to transfer these licences to a Successor, incorporating would mean
re-contacting every contributor for permission, which in practice does not
happen. Asking once, at the start, is the alternative to the commercial tier
quietly becoming impossible.

A copyright *assignment* would achieve the same thing and take Your copyright
with it. This agreement deliberately does not do that.

## 6. You are entitled to grant this licence

You represent that You are legally entitled to grant the above licences. If Your
employer has rights to intellectual property that You create, including Your
Contributions, You represent that You have received permission to make the
Contributions on behalf of that employer, that Your employer has waived such
rights for Your Contributions, or that Your employer has executed a separate
Corporate CLA with the Maintainer.

## 7. Your Contributions are Your original creation

You represent that each of Your Contributions is Your original creation. You
represent that Your Contribution submissions include complete details of any
third-party licence or other restriction (including, but not limited to, related
patents and trademarks) of which You are personally aware and which are
associated with any part of Your Contributions.

Contributions to `revela/sensors/` carry an additional, specific requirement:
register tables **must not** be transcribed out of the Linux kernel media drivers
(`drivers/media/i2c/imx219.c` and its siblings), which are licensed GPL-2.0 and
whose terms are incompatible with this Project's licence. See
[CONTRIBUTING.md](CONTRIBUTING.md).

## 8. No support obligation, and no warranty

You are not expected to provide support for Your Contributions, except to the
extent You desire to provide support. You may provide support for free, for a
fee, or not at all.

Unless required by applicable law or agreed to in writing, You provide Your
Contributions on an **"AS IS" BASIS, WITHOUT WARRANTIES OR CONDITIONS OF ANY
KIND**, either express or implied, including, without limitation, any warranties
or conditions of TITLE, NON-INFRINGEMENT, MERCHANTABILITY or FITNESS FOR A
PARTICULAR PURPOSE.

## 9. Third-party work

Should You wish to submit work that is not Your original creation, You may submit
it to the Maintainer separately from any Contribution, identifying the complete
details of its source and of any licence or other restriction (including, but not
limited to, related patents, trademarks and licence agreements) of which You are
personally aware, and conspicuously marking the work as
"Submitted on behalf of a third party: [named here]".

## 10. Notification

You agree to notify the Maintainer of any facts or circumstances of which You
become aware that would make these representations inaccurate in any respect.

## 11. Marks

This agreement grants You no right to use the name "revela", any logo of the
Project, or any other trade name, trademark or service mark of the Maintainer.
See [TRADEMARK.md](TRADEMARK.md).

## 12. Governing law

This agreement is governed by the law of England and Wales, and the courts of
England and Wales have exclusive jurisdiction over any dispute arising out of it.

*(This clause in particular is provisional and subject to the legal review noted
at the top of this document.)*

---

## Signing

Open your first pull request. The CLA assistant will comment on it with a link,
and one signature covers all of your future contributions to the Project.

If you cannot sign — many employment contracts make this genuinely complicated —
please say so in the pull request. Small fixes can often be taken as suggestions
and reimplemented, and a bug report with a failing test case is enormously
valuable and needs no CLA at all.

| Field | |
| --- | --- |
| Full name | |
| GitHub username | |
| Email | |
| Postal address | |
| Date | |
| Signature | |
