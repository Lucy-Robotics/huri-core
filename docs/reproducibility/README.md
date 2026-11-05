# Reproducibility test

Can someone outside the team install HuRI and run it from the docs alone, on a
clean machine? This page is the protocol of that test. Each run ends with a
dated report in this folder.

| Report | Who | Machine | Result |
|---|---|---|---|
| [2026-10-31, dry run in a clean container](2026-10-31-clean-container-dry-run.md) | the team, before the outsider test | Ubuntu 24.04 container, CPU only | part A: 5 of 6 steps passed; the HuRI-CLI step failed on its README, fixed |

## Who and where

- **The tester** is not on the team and has never contributed to HuRI. They
  know Linux, git and Python. Nobody from the team helps them during the test
  unless they are completely stuck. Each time someone helps, the report says
  so.
- **The machine** is clean: a fresh OS install, a new virtual machine, or at
  least a new user account with no HuRI file, no virtualenv and no pip cache.
  Ubuntu 24.04 is the reference. Another distribution is fine; the report says
  which.
- **Part A** needs no GPU, 4 GiB of free disk and about 15 minutes. **Part B**
  installs the whole pipeline: 16 GiB of RAM, 10 GiB of free disk and about an
  hour, mostly downloads. A GPU is optional for both.

## Rules

1. Follow [docs/INSTALL.md](../INSTALL.md) and the READMEs it links to, and
   nothing else: no help from memory, no command from elsewhere. Read them on
   the `dev` branch of each repository until the next release.
2. Copy each command as written; replace only the placeholders (`<ref>`).
3. Write down every deviation: a command that failed, a step you had to guess,
   a package you had to install that the docs do not mention, a sentence you
   did not understand.
4. Time each step, wall clock.
5. Fill in the [report template](report-template.md), and open an issue for
   each problem found.

## Part A: framework, module and client (CPU)

| Step | Docs to follow | Passes when |
|---|---|---|
| A0 | [INSTALL.md, Prerequisites](../INSTALL.md#prerequisites) | `python3 --version` is 3.10 to 3.12, and `git` and `make` are installed |
| A1 | [INSTALL.md, path 2](../INSTALL.md#2-the-framework-huri-core): work on huri-core, at `dev` | `make check` ends with no failure |
| A2 | the [minimal end to end example](../../README.md#a-minimal-end-to-end-example), in a new virtualenv | the client prints `HELLO!` |
| A3 | [INSTALL.md, path 3](../INSTALL.md#3-a-module-of-your-own), with `huri-plugin-template` cloned at `dev` (instead of **Use this template**) | `pytest` passes in the renamed module |
| A4 | HuRI-CLI's [Working on huri-cli](https://github.com/Lucy-Robotics/HuRI-CLI#working-on-huri-cli), at `dev` | `pytest -q` passes and `huri-cli --help` prints the usage |
| A5 | huri-modules' [Quick start](https://github.com/Lucy-Robotics/huri-modules#quick-start), first command only, at `dev` | `./install.sh --plan-only` prints a plan and a `VERDICT` line |

## Part B: the pipeline

| Step | Docs to follow | Passes when |
|---|---|---|
| B1 | [INSTALL.md, path 1](../INSTALL.md#1-the-pipeline-huri-modules-installer): `./install.sh` | the installer ends without an error |
| B2 | `.huri-local/start.sh`, then `.huri-local/status.sh` | `start.sh` exits 0; `status.sh` shows the application `RUNNING` |
| B3 | `huri-cli --config config/client_local.generated.yaml`, type `What is HuRI?` | an answer is printed within 30 seconds, and spoken when the plan has TTS |
| B4 | the same, speaking instead of typing, when the plan has speech in | the question is transcribed and answered |
| B5 | `make check` in the huri-modules checkout | no failure |
| B6 | `.huri-local/stop.sh --all` | nothing of HuRI is left running (`.huri-local/status.sh`) |

## After the test

The tester, or a team member with the tester's notes, adds the report as
`YYYY-MM-DD-<who-or-machine>.md` in this folder and a line in the table above,
in a pull request. Each problem gets an issue, labelled `bug` or
`documentation`, linked from the report. Fixes go in their own pull requests.

## The dry run

`scripts/reproducibility_dry_run.sh [OUT_DIR]` runs part A in a fresh
`ubuntu:24.04` Docker container, with the commands of the docs, and writes one
log per step and `summary.tsv` to `OUT_DIR`. It checks the docs before an
outsider spends time on them; it does not replace the outsider. The container
uses the host's CPU and RAM, and part B is not in it.
