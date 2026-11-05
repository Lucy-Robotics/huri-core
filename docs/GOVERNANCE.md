# Governance

Who decides what in HuRI: what anyone can propose, what the maintainers keep
under their control, and why.

## The maintainers

The maintainers are the members of the
[Lucy-Robotics](https://github.com/Lucy-Robotics) organisation with write access
to HuRI's repositories. They review and merge pull requests, triage issues and
release.

Decisions are taken in the open, in the issue or the pull request concerned,
with the reason written down. A question that needs a decision before work can
start is an issue labelled `discussion`. When the maintainers do not agree, the
team decides together and records the outcome in that issue.

## Open to proposals

Anyone can propose these, by an issue or directly by a pull request, following
[CONTRIBUTING.md](../CONTRIBUTING.md).

| What | How | Why it is open |
|---|---|---|
| A module of your own | your own repository, from [huri-plugin-template](https://github.com/Lucy-Robotics/huri-plugin-template). Nobody's permission is needed to publish it and use it. huri-core and the template are GPL-3.0, so a module you distribute is GPL-3.0 too | HuRI is meant to be extended by others; a module runs only where an operator enables it |
| New event types | your own events package, in your own topic namespace (`acme.*`) | a namespace of your own cannot break anyone else's |
| Fixes and improvements to the curated modules, the hub, the installer, the Docker images and the Helm chart | pull request to huri-modules | they are tested in CI and versioned per package, so a change is checked and visible |
| Fixes and improvements to the framework that keep its public surface | pull request to huri-core | the tests and the semver rules protect the users |
| The reference client and the module template | pull request to HuRI-CLI or huri-plugin-template | they are examples; better examples help everyone |
| Docs, tests, CI, bug reports, feature requests | an issue or a pull request in the repository concerned | they cost the users nothing and help everyone |
| Listing a module as verified | pull request to huri-modules' `registry.toml`, following [ACCEPTANCE.md](https://github.com/Lucy-Robotics/huri-modules/blob/main/ACCEPTANCE.md) | the criteria are public and CI checks the mechanical part |
| A use case built on HuRI | your own repository; a pull request adds it to [Built with HuRI](../README.md#built-with-huri) | HuRI exists to be used |

## Kept by the maintainers

Anyone can propose a change to these too, in an issue labelled `discussion`.
The maintainers decide, and a pull request on them waits for that decision.

| What | Why |
|---|---|
| huri-core's public surface (base classes, client SDK, session schema, wire protocol, entry point groups, manifest; listed in [RELEASING.md](../RELEASING.md)) and when it breaks | modules, event packages and clients pin a huri-core range. A break must be deliberate, announced and released as a minor version |
| The `huri.*` topics and the wire formats of huri-events | every curated module and every client speaks them, robots included. A change there breaks deployed clients |
| The safety defaults: modules are denied unless the server lists them, actuation needs the operator's second consent (`HURI_ACTUATION_MODULES`), manifests and version ranges are checked at startup | HuRI can drive hardware near people. Weakening a safety check is never a side effect of another change |
| Which modules join the curated (official) set, as packages under huri-modules' `packages/` | the team releases, tests and maintains the curated set, and installs it everywhere: each module there is a long-term cost to the team |
| The verified mark: who gets it and the criteria in ACCEPTANCE.md | it tells users that the team reviewed a module. Its value depends on the team granting it |
| Releases: version numbers, tags, what goes to `main`, PyPI | a release is a promise to the users about what works with what |
| The pinned versions: `versions.env`, `constraints.txt`, the default models | they decide what installs and runs together; the installer and CI are tested against them |
| Merge rights, branch rules, required CI checks, secrets and the GitHub settings | they protect the code and the release pipeline |
| The roadmap: which issues the team works on, and when | the team answers for its commitments and its time |
| The licence, GPL-3.0 | contributions are accepted under the repository's licence, GPL-3.0, with no other agreement to sign. Changing it would need every contributor's consent |

## Changing this page

This page changes like any other: a pull request to huri-core. The maintainers
decide on it.
