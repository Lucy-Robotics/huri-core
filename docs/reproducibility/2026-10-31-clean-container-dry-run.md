# Reproducibility report: 2026-10-31, dry run in a clean container

## Summary

- **Date**: 2026-10-31
- **Tester**: the team (run with Claude Code), before the outsider test. This
  is not the outsider test: it checks that part A of the
  [protocol](README.md) can pass before someone outside the team spends time
  on it.
- **Helped by the team**: not applicable
- **Result**: part A, first run: 5 of 6 steps passed. The HuRI-CLI step failed
  because its README installs two versions that cannot go together. Second
  run, with the command CI uses: 6 of 6. Part B was not run.

## Machine

| | |
|---|---|
| Kind | Docker container, `ubuntu:24.04` image, fresh for each run, `--shm-size=2g` |
| OS | Ubuntu 24.04.5 LTS |
| CPU, RAM | the host's: 16 cores, 30.7 GiB |
| GPU | none visible in the container |
| Free disk | 10 GiB |
| Python | 3.12.3 (`python3.12` from apt) |

## Versions tested

| Repository | Ref | Commit |
|---|---|---|
| huri-core | dev | `3ad96d9` |
| huri-modules | dev | `68d251b` |
| HuRI-CLI | dev | `17500c4` |
| huri-plugin-template | dev | `fbfff91` |
| huri-core, for A2 and A3 | tag `0.4.0` | |
| huri-events, for A3 | huri-modules tag `0.1.0` | |

## Steps

The commands are those of `scripts/reproducibility_dry_run.sh`, copied from
the docs. Times are those of the first run.

| Step | Result | Time | Deviations and notes |
|---|---|---|---|
| A0 | pass | 26 s | `apt-get install python3.12 python3.12-venv python3.12-dev python3-pip git make libportaudio2 curl` |
| A1 | pass | 63 s | `make check`: lint and link check clean, 149 tests passed |
| A2 | pass | 55 s | the client printed `HELLO!` |
| A3 | pass | 54 s | `./rename.sh servo`, `./install.sh` (variant `cpu`), `pytest`: 2 passed |
| A4 | **fail**, then pass | 11 s | see problem 1. With huri-core `0.4.0`: 19 tests passed, `huri-cli --help` printed the usage |
| A5 | pass | 1 s | a CPU-only plan: speech in and out, `qwen2.5:14b` on Ollama, no gesture |

The second run of the whole script, at 18:30, passed the 6 steps in 3 minutes
10 seconds.

## Problems found

| # | Step | What happened | Fix |
|---|---|---|---|
| 1 | A4 | HuRI-CLI's README, [Working on huri-cli](https://github.com/Lucy-Robotics/HuRI-CLI#working-on-huri-cli), installs huri-core at `dev` with huri-events at `dev`. pip stops with `ResolutionImpossible`: huri-core's `dev` is `0.5.0.dev0`, and huri-events 0.1.0 requires `huri-core>=0.4,<0.5`. CI never saw it: it installs huri-core `0.4.0`. | HuRI-CLI pull request from `mvr/fix-dev-install-readme`: the README installs huri-core `0.4.0`, as CI does, and says why |
| 2 | A0 | `make check` needs `make`, which no prerequisites list named; PortAudio, which huri-cli needs, was only in HuRI-CLI's README. | [docs/INSTALL.md](../INSTALL.md#prerequisites) lists both |

## Not covered

- Part B, the pipeline: about 8 GiB of downloads and 16 GiB of RAM. The plan
  step (A5) ran; the install did not.
- A GPU: the container saw none.
- A person outside the team: the container followed the docs to the letter,
  which a person does not. The outsider test is still to do.
