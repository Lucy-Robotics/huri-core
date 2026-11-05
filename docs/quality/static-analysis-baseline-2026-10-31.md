# Static analysis baseline, 2026-10-31

The warnings and errors the static analysers report on HuRI's four core
repositories when the quality work started. Later reports run the same
commands ([How to reproduce](#how-to-reproduce)) and compare with the tables
below.

Taken on `dev`, where pull requests land. `main` is a few commits behind or
ahead in each repository (docs and version bumps); the `main` commits are
listed so the numbers can be checked there too.

## What was analysed

| Repository | `dev` | `main` | Python lines (without tests) | Shell scripts |
|---|---|---|---|---|
| huri-core | [3ad96d9](https://github.com/Lucy-Robotics/huri-core/commit/3ad96d9) | [0549e58](https://github.com/Lucy-Robotics/huri-core/commit/0549e58) | 1,929 | none |
| huri-modules | [68d251b](https://github.com/Lucy-Robotics/huri-modules/commit/68d251b) | [403d68d](https://github.com/Lucy-Robotics/huri-modules/commit/403d68d) | 5,791 | 5 |
| huri-cli | [17500c4](https://github.com/Lucy-Robotics/huri-cli/commit/17500c4) | [f00c519](https://github.com/Lucy-Robotics/huri-cli/commit/f00c519) | 449 | none |
| huri-plugin-template | [fbfff91](https://github.com/Lucy-Robotics/huri-plugin-template/commit/fbfff91) | [fae3839](https://github.com/Lucy-Robotics/huri-plugin-template/commit/fae3839) | 29 | 2 |

The tools, each with the version and configuration its repository's CI uses
(`pyproject.toml`), on Python 3.12:

| Tool | Checks | Version |
|---|---|---|
| black | formatting | 26.3.1 (template: 26.10.1) |
| isort | import order | 8.0.1 (template: 9.0.2) |
| flake8 | style and pyflakes errors | 7.3.0 (template: 7.5.0) |
| mypy | types, `--check-untyped-defs` | 1.20.2 (template: 2.4.1) |
| bandit | security, on the code that ships (tests excluded) | 1.9.4 |
| shellcheck | shell scripts, every level | 0.11.0 |

The template pins its tools with `>=`, so it got the latest releases.

## Results

| Repository | black | isort | flake8 | mypy | bandit (high / medium / low) | shellcheck (warning / info) |
|---|---|---|---|---|---|---|
| huri-core | 0 (38 files) | 0 | 0 | 0 (21 files) | 0 / 1 / 2 | no scripts |
| huri-modules | 0 (75 files) | 0 | 0 | 1 (44 files) | 1 / 3 / 15 | 6 / 2 |
| huri-cli | 0 (11 files) | 0 | 0 | 0 (7 files) | 0 / 0 / 2 | no scripts |
| huri-plugin-template | 0 (5 files) | 0 | 0 | 0 (3 files) | 0 / 0 / 0 | 0 / 0 |
| **Total** | **0** | **0** | **0** | **1** | **1 / 4 / 19** | **6 / 2** |

No shellcheck error and no `style` comment anywhere.

Formatting, imports and flake8 are clean because CI already blocks on them.
Every finding above comes from a check that CI does not block on or does not
run:

| Repository | CI blocks on | CI runs without blocking | CI does not run |
|---|---|---|---|
| huri-core | black, isort, flake8, mypy, the link check | the deep-link check | bandit |
| huri-modules | black, isort, flake8, mypy on the files already clean (28 files, 0 errors), shellcheck errors | mypy on all of `packages/`, shellcheck warnings | bandit |
| huri-cli | black, isort, flake8, mypy | | bandit |
| huri-plugin-template | black, isort, flake8 | | mypy, shellcheck, bandit |

## Findings

### High

- **TLS certificates are not checked by the ingestion tool's embedder.**
  [huri-modules `huri_rag/ingestion.py:39`](https://github.com/Lucy-Robotics/huri-modules/blob/68d251b/packages/huri-module-rag/huri_rag/ingestion.py#L39)
  creates `httpx.Client(verify=False)` for `RemoteEmbedder`. The same tool has
  a `--no-verify-ssl` flag and honours it for Qdrant, but the embedder ignores
  it: an HTTPS embeddings endpoint is never verified. (bandit B501)

### Medium

- **The EMO model is downloaded without a pinned revision.**
  [huri-modules `huri_emo/module.py:34-35`](https://github.com/Lucy-Robotics/huri-modules/blob/68d251b/packages/huri-module-emo/huri_emo/module.py#L34)
  calls `from_pretrained(model_name)` with no `revision=`, so a change on the
  Hugging Face repository changes the model HuRI runs. (bandit B615, twice)
- **`urlopen` could open `file:` URLs: a false positive here.**
  [huri-modules `huri_hub/web.py:53`](https://github.com/Lucy-Robotics/huri-modules/blob/68d251b/packages/huri-hub/huri_hub/web.py#L53)
  gets only the `https://` URLs the hub builds, or a `--registry` /
  `HURI_REGISTRY` location already checked to start with `http://` or
  `https://`;
  [huri-core `scripts/check_links.py:158`](https://github.com/Lucy-Robotics/huri-core/blob/3ad96d9/scripts/check_links.py#L158)
  fetches only the pages' `http(s)` links, and only with `--external`. A scheme
  check inside `http_fetch` would make that explicit and silence bandit.
  (bandit B310, twice)

### Low

| Rule | Count | Where | Note |
|---|---|---|---|
| B101 `assert` in shipped code | 8 | huri-core `manifest.py:149-150`; huri-modules `mov/emage/processing.py:158,177`, `tts/cosyvoice/handle.py:214`, `tts/streaming.py:83`, `scripts/module_requirements.py:82`; huri-cli `scripts/check_config_modules.py:35` | `python -O` removes asserts; the two in `manifest.py` come after the validation and only narrow types for mypy |
| B603, B607, B404 `subprocess` | 9 | huri-modules `check_registry.py`, `huri_hub/cli.py` | `git`, `pytest` and `pip install` run with argument lists, no shell: expected for a registry checker and an installer |
| B110 `try` / `except` / `pass` | 3 | huri-modules `rag/rag.py:302,378`; huri-cli `audio.py:96` | errors dropped silently |

### Types

- [huri-modules `huri_rag/rag.py:873`](https://github.com/Lucy-Robotics/huri-modules/blob/68d251b/packages/huri-module-rag/huri_rag/rag.py#L873):
  unused `type: ignore`, with the lint job's packages (no torch, no
  transformers). It is the only error left in `mypy packages`: the errors in
  `semantic_chunker.py` and `ingestion.py` that the CI comment mentions are
  gone, so the full run could block once this one is fixed.

### Shell scripts (huri-modules)

| Where | Rule | What |
|---|---|---|
| `scripts/test_install.sh:96` | SC2318 (warning) | in `local target="$1" dir="$WORK_BASE/$target"`, bash expands `$target` before `local` sets it. It works only because the caller's loop variable is also named `target`; rename that loop variable and `dir` becomes `$WORK_BASE/`, which the `rm -rf` guard lets through: a latent bug |
| `scripts/install_local.sh:1748` | SC2155 (warning) | `local x=$(...)` hides the command's exit status |
| `scripts/install_local.sh:81,87,140,2013` | SC2034 (warning) | unused variables (`HURI_CORE_REF_FROM`, `HURI_CLI_REF_FROM`, `VRAM_TTS_FP32`, `i`) |
| `scripts/install_local.sh:2990`, `scripts/test_install.sh:155` | SC2015 (info) | `A && B \|\| C` used as if-then-else |

## How to reproduce

In a checkout of each repository at the commit above, in a Python 3.12
virtualenv:

```sh
pip install -c constraints.txt black==26.3.1 isort==8.0.1 flake8==7.3.0 \
  flake8-toml-config==1.0.0 mypy==1.20.2 bandit shellcheck-py
black --check <lint paths>
isort --check-only <lint paths>
flake8 <lint paths>
mypy <mypy arguments>
bandit -q -r <bandit paths> -x '*/tests/*'
shellcheck -x <scripts>
```

| Repository | Also install | Lint paths | mypy arguments | bandit paths | Scripts |
|---|---|---|---|---|---|
| huri-core | | `huri_core scripts tests` | `huri_core scripts --check-untyped-defs` | `huri_core scripts` | |
| huri-modules | `--no-deps "huri-core @ git+https://github.com/Lucy-Robotics/huri-core.git@0.4.0"` (its `HURI_CORE_REF`) | `packages tests check_registry.py scripts` | `packages --check-untyped-defs` | `packages check_registry.py scripts deploy` | `install.sh scripts/*.sh deploy/install_huri.sh packages/huri-module-tts/install_cosyvoice.sh` |
| huri-cli | `"huri-core[client] @ git+https://github.com/Lucy-Robotics/huri-core.git@0.4.0"`, `"huri-events @ git+https://github.com/Lucy-Robotics/huri-modules.git@dev#subdirectory=packages/huri-events"`, then `-e ".[dev]"` | `huri_cli tests scripts` | `huri_cli scripts --check-untyped-defs` | `huri_cli scripts` | |
| huri-plugin-template | `flake8-pyproject` instead of the pins above, no `-c` | `.` | `huri_example events --check-untyped-defs --ignore-missing-imports` | `huri_example events` | `install.sh rename.sh` |

The huri-modules files mypy blocks on in CI (`mypy --check-untyped-defs
--follow-imports=silent` on `huri_events`, `huri_tts`, `huri_vad`, `huri_stt`,
`huri_tag`, `huri_hub` and `check_registry.py`) are 28 files with 0 errors.
