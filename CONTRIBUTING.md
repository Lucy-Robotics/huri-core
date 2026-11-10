# Contributing to HuRI

HuRI spans several repositories (see [HuRI ecosystem](README.md#huri-ecosystem)),
plus the use cases built on it; pick the line that matches what you want to
change, and open the issue or pull request there.

| You want to… | Repository | How |
|---|---|---|
| Add your own module (a new capability) | your own, created from [huri-plugin-template](https://github.com/Lucy-Robotics/huri-plugin-template) | nobody's permission needed (huri-core and the template are GPL-3.0, so a module you distribute is GPL-3.0 too): publish it, tag it `huri-module` to be found by `huri hub search`; for the verified mark, open a pull request to [huri-modules](https://github.com/Lucy-Robotics/huri-modules) adding it to `registry.toml` and pinning it as a submodule under `modules/<name>` ([ACCEPTANCE.md](https://github.com/Lucy-Robotics/huri-modules/blob/main/ACCEPTANCE.md)) |
| Add an event type | an events package of your own, in your own topic namespace (`acme.*`) | see the template's `events/`; `huri.*` is reserved for [huri-events](https://github.com/Lucy-Robotics/huri-modules/tree/main/packages/huri-events) |
| Add, fix or improve a curated (official) module, or `huri-events` | [huri-modules](https://github.com/Lucy-Robotics/huri-modules) ([issues](https://github.com/Lucy-Robotics/huri-modules/issues)) | pull request on `dev` to that package under `packages/` (a new module: first an issue labelled `discussion`, as the maintainers decide which modules join the curated set; then `packages/huri-module-<name>` and its `registry.toml` entry), with tests (`huri_core.testing`); bump the package's version |
| Change the hub (`huri hub`), the registry's format or its checker | [huri-modules](https://github.com/Lucy-Robotics/huri-modules) ([issues](https://github.com/Lucy-Robotics/huri-modules/issues)) | pull request on `dev` to `packages/huri-hub` (the registry parser lives there; `check_registry.py` reuses it), with tests; bump the package's version |
| Change install or deployment | [huri-modules](https://github.com/Lucy-Robotics/huri-modules) | pull request on `dev`; CI plans the installer on three distros (`ci.yml`); a change to `deploy/` or `packages/` also builds the base image and runs a session against it (`docker.yml`); the GPU images and a real install run on demand (`docker.yml`'s `gpu_images`, `install.yml`) |
| Change the framework (bus, server, SDK, loader, the `huri` command dispatcher) | [huri-core](https://github.com/Lucy-Robotics/huri-core) ([issues](https://github.com/Lucy-Robotics/huri-core/issues)) | pull request on `dev`; mind the public surface and semver ([RELEASING.md](RELEASING.md)) |
| Fix or improve the reference client (`huri-cli`), its example configs or the `huri` metapackage | [HuRI-CLI](https://github.com/Lucy-Robotics/HuRI-CLI) ([issues](https://github.com/Lucy-Robotics/HuRI-CLI/issues)) | pull request on `dev`, with tests; CI runs pytest and lint on Python 3.10–3.12 against `huri-core` and `huri-modules` `dev` |
| Improve the module starter | [huri-plugin-template](https://github.com/Lucy-Robotics/huri-plugin-template) | pull request on `dev` |
| Improve the HuRI-wide pages (the README's ecosystem section, this file, [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)) | [huri-core](https://github.com/Lucy-Robotics/huri-core) | pull request on `dev`; CI runs `python scripts/check_links.py` |
| Fix or improve a use case (InMoov, the acceptance test site, ...) | its own repository, e.g. [HuRI-Lucy](https://github.com/Lucy-Robotics/HuRI-Lucy) | that repository's own process |

The pages of huri-core (README.md, CONTRIBUTING.md, RELEASING.md, docs/)
link into `huri-modules` and `HuRI-CLI` at `main`. `python
scripts/check_links.py` checks every relative link and anchor, and that each
`Lucy-Robotics` URL names a known repository; with `--siblings DIR` it also
resolves each deep link (`tree/<ref>/<path>`, `#anchor`, `#subdirectory=`)
against checkouts in `DIR/<repo>`.

A security or safety flaw is not an issue: report it in private, as
[SECURITY.md](SECURITY.md) says.

Not sure where something belongs? Open an issue on
[huri-core](https://github.com/Lucy-Robotics/huri-core/issues) and it will be
moved.

What anyone can propose, what the maintainers decide, and why:
[docs/GOVERNANCE.md](docs/GOVERNANCE.md).

In `huri-core` and `huri-modules`, run `make check` locally first. CI runs the
same lint (black, isort, flake8, mypy) and pytest as separate steps, not
`make check` itself; in `huri-modules`, mypy gates only the files that are
already clean.

In the four repositories, CI runs on every push, to any branch, and on every
pull request: static analysis, formatting, tests and a build. Every run, passed
or failed, is listed with its logs in the repository's Actions tab:
[huri-core](https://github.com/Lucy-Robotics/huri-core/actions/workflows/ci.yml),
[huri-modules](https://github.com/Lucy-Robotics/huri-modules/actions/workflows/ci.yml),
[HuRI-CLI](https://github.com/Lucy-Robotics/HuRI-CLI/actions/workflows/ci.yml),
[huri-plugin-template](https://github.com/Lucy-Robotics/huri-plugin-template/actions/workflows/ci.yml).

## Versioning, in short

- `huri-core`, `huri-modules` and `huri-cli` each follow semver, with tags
  `X.Y.Z` (no `v`) on `main` and work on `dev`. While `0.x`, a minor bump may
  break the public surface; a patch must not.
- A change to `huri-core`'s public surface is a minor bump; `huri-modules`
  sees it first in its non-blocking canary CI (against `huri-core` `dev`), then
  adopts it by bumping `HURI_CORE_REF` in `versions.env` in a pull request.
- In `huri-modules`, each package has its own version in its `pyproject.toml`:
  bump it when the package changes. A wire-format change to `huri-events` is a
  minor bump while `0.x`.
- Working on `huri-core` and `huri-modules` together: set
  `HURI_CORE_PATH=/path/to/huri-core` and `huri-modules` installs that checkout
  (editable) instead of `HURI_CORE_REF`; likewise `HURI_CLI_PATH` for a
  `huri-cli` checkout instead of `HURI_CLI_REF`.

Details: [huri-core's RELEASING.md](RELEASING.md),
[huri-modules' RELEASING.md](https://github.com/Lucy-Robotics/huri-modules/blob/main/RELEASING.md),
and [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md#versions-and-how-a-core-change-propagates).

## Submitting a change

1. **Start from an issue** for anything bigger than a typo: comment on it so
   nobody else starts the same work, or open one and say what you plan. Issues
   labelled `good first issue` are small and self-contained.
2. **Branch from `dev`.** Team members branch in the repository, named
   `<initials>/#<issue>/<short-name>` (`mvr/#58/fix-install`), or
   `<initials>/<short-name>` when there is no issue. Anyone else
   forks the repository and branches in the fork; the name is free.
3. **Commit** with the [format below](#commit-messages).
4. **Run the checks locally**: `make check` in huri-core and huri-modules;
   in HuRI-CLI and huri-plugin-template, the lint and test commands of their
   README.
5. **Open a pull request to `dev`** and fill in its template: what changes and
   why, the issue it closes (`Closes #58`) and how you tested it.
6. **CI must pass.** Every repository runs it on each push and pull request,
   and `dev` and `main` refuse a merge while a required check fails.
7. **Another person approves.** Every pull request into `dev` or `main` needs
   the approval of a maintainer who is not its author, and a new push
   dismisses an earlier approval. Nobody merges their own work unreviewed,
   maintainers included.

`main` only moves at a release: maintainers merge `dev` into it and tag it
([RELEASING.md](RELEASING.md)).

## Conventions

- **Python 3.10 to 3.12.** Code must run on all three; CI tests each one.
- **Style** is what the tools say, configured in each `pyproject.toml`:
  black and isort (line length 88, isort's `black` profile), flake8 and mypy.
  `make lint` in huri-core and `make format` in huri-modules fix the first two.
- **Tests** with pytest. A fix comes with a test that fails without it; a new
  feature comes with tests for it. A module is tested with
  `huri_core.testing` (no Ray, no GPU), and the tests must not reach the
  network or need a model download. [docs/TESTING.md](docs/TESTING.md) has
  the critical path, the levels, the coverage targets and the rest of the
  rules.
- **Docs** change in the same pull request as the behaviour they describe;
  `python scripts/check_links.py` keeps the links valid.
- **Versions**: bump the version of each package you change in huri-modules,
  and follow the [versioning rules above](#versioning-in-short).
- **No secrets** in the code, the configs or the history: API keys go in
  `.huri-local/secrets.env` or the environment.
- Code, commits, issues and pull requests are written in English.

## Commit messages

[Conventional Commits](https://www.conventionalcommits.org/en/v1.0.0/):

```text
<type>(<scope>): <summary>

<body: why, when the summary is not enough>

Closes #58
```

- **type**: `feat` (a feature), `fix` (a bug fix), `docs`, `test`,
  `refactor` (no behaviour change), `perf`, `ci`, `build` (packaging,
  dependencies), `chore`, `release`.
- **scope**, optional: the package or the area, as in `fix(installer):`,
  `docs(readme):`, `feat(hub):`.
- **summary**: imperative, lower case, no final period, 72 characters at most.
- A breaking change adds `!` after the type or scope (`feat(events)!:`) and a
  `BREAKING CHANGE:` line in the body; it means a minor bump while `0.x`.

Older commits use `evol:` for features; new ones use `feat:` or `refactor:`.

## Response times

The maintainers are the Lucy-Robotics team. We aim to:

| What | First answer within |
|---|---|
| a new issue: labelled, and answered or assigned | 7 days |
| a pull request: a first review | 7 days |
| a question in a review or an issue | 7 days |

CI answers at once. If a week has passed without a reply, comment again on the
issue or pull request.

## Labels

| Label | Meaning |
|---|---|
| `bug` | something does not work as documented |
| `enhancement` | a new feature or an improvement |
| `documentation` | the docs only |
| `refactor` | a change of structure with no change of behaviour |
| `question` | a question, not a change request |
| `discussion` | a decision to take before work can start |
| `epic` | tracks several issues |
| `good first issue` | small and self-contained: a good start |
| `help wanted` | the maintainers welcome a pull request for it |
| `needs triage` | new, not yet looked at by a maintainer |
| `blocked` | waits on another issue or a decision |
| `duplicate`, `wontfix` | closed without a change, with the reason in a comment |
