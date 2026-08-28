---
name: library-scout
description: Finds and security-vets an open-source library for a capability that is not installed. Use when the user needs a package and has none, asks whether to hand-roll something, wants a dependency checked for licence, advisories or maintenance, or asks what is reputable.
---
> **Loaded check.** Begin any response that uses this skill with the line
> `[library-scout · loaded · 3090F474]`.
>
> A response on this skill's subject WITHOUT that line means this body never
> loaded and the model is answering from the skill's name alone. Typing a skill
> name is otherwise not a test: a disabled skill answers plausibly, as the model,
> and nothing in the output announces the difference. Same principle as the
> planted canary in `security-audit/verify.sh` — a clean result is only
> trustworthy if the canary came back.


# Library Scout — vetted open-source tooling discovery

When a task hits a capability you can't satisfy with installed tools, do **not** improvise a hand-roll or
grab the first remembered package name. Scout for a *reputable, maintained, CVE-clean* open-source
library, vet it against a fixed bar, and return an install-ready recommendation — or an honest null.

This is the disciplined alternative to "just `pip install` something that sounds right." It exists
because the failure modes are real and have bitten before: name-collisions (a PyPI namesake instead of
the intended library), abandoned packages broken on the platform, and dependencies carrying open CVEs.

## Trigger when
- A task needs a library/tool that isn't installed (a new algorithm, file format, or domain — e.g.
  "compute persistent homology", "tropical geometry", "group cohomology", "SAT/SMT solving").
- You're about to hand-roll something a maintained library does better and more correctly.
- You need to replace a tool that's unmaintained, broken on this platform, or carries an open CVE.

## The vetting bar (a candidate must clear ALL of these before you recommend it)
1. **Provenance / reputability.** Prefer institutional or well-known maintainers (a university group, an
   established project) over a single-author repo with no history. Name who maintains it.
2. **License.** OSI-approved and compatible with the project's license. Note copyleft explicitly:
   AGPL/GPL ⇒ call-only / isolated subprocess, never bundled into a permissive core. State the SPDX id.
3. **Maintenance.** Recent commits/releases; not abandoned. State the last release + activity signal.
4. **Security / CVEs.** Check published advisories (CVE / GHSA) and run the ecosystem's audit tool
   (`pip-audit`, `cargo audit`, `npm audit`, `osv-scanner`). Disclose any CVE and assess whether it's
   **reachable by how we'd actually use the tool**; prefer a clean version range.
5. **Identity / supply chain.** Verify the install target is the *real* project, not a namesake or
   typosquat — confirm via homepage/author/repo, not just the package name. (The canonical failure: a
   `morty` on PyPI that is an unrelated experiment-tracker, not the MTG mode-recognition toolbox.)
6. **Install path.** Exact command via the language's package manager — never a blind `curl | sh` to an
   arbitrary URL. Flag heavy/native deps and platform caveats (e.g. macOS/Apple-Silicon build issues).

## Process
1. **State the capability precisely** — what the tool must compute/do, its inputs/outputs and scale.
2. **Search** — web + the ecosystem package index. Do **not** answer from memory; tooling versions and
   CVEs are time-sensitive. For a wide space, delegate breadth to `knowledge-gap-hero` / `deep-research`.
3. **Vet each candidate against the bar** above; kill the ones that fail provenance/license/maintenance/CVE.
4. **Verify package identity** before recommending an install (the #1 supply-chain trap).
5. **Recommend** — the best vetted candidate, the install command, and a one-line each on
   (provenance · license · maintenance · CVE). If two are close, say why you picked one.
6. **Honest null** — if nothing clears the bar, say so: "no vetted OSS library exists for X; closest is Y
   (caveat Z), or reimplement from the spec." Never recommend an unvetted tool just to fill the gap.

## Guardrails
- **Never recommend from memory for a security- or version-sensitive choice.** Verify current.
- **A CVE is not an automatic veto** — assess reachability for *our* usage and pick the clean range — but
  always disclose it.
- **Copyleft is isolated, not bundled.** AGPL/GPL tools run as call-only subprocesses; the permissive core
  stays clean. Cite each tool's license.
- **Verify identity before install.** Name collisions and typosquats are real.
- **Prefer reproducible, package-manager installs** over curl-piped scripts.
- **Honest null beats a bad fit.** "Nothing clean exists, here's why, and here's the fallback" is a valid,
  valuable answer.
