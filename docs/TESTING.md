# Testing policy

What HuRI must never break, how each part is tested, what runs by itself and
what a person runs, how much of the code the tests reach, and the rules a test
follows. It covers huri-core and huri-modules; huri-plugin-template gives
each new module the same setup.

## The critical path

HuRI listens to a person, answers by voice and can move a robot. A failure on
these paths either stops the conversation or moves hardware when it should
not. Each item has at least one test that fails when it breaks.

| # | What must hold | Tested by |
|---|---|---|
| 1 | **Safety defaults**: the server loads only the modules it lists, a module that moves hardware also needs `HURI_ACTUATION_MODULES`, a module whose manifest or version range does not match is refused at startup | huri-core `test_actuation.py`, `test_compat.py`, `test_manifest.py`; huri-modules `test_curated_plugins.py` |
| 2 | **A session**: a client connects, the handshake agrees on topics, JSON and binary frames reach the right module and come back, a bad request is rejected | huri-core `test_server_session.py`, `test_client_sdk.py`, `test_topic_wiring.py` |
| 3 | **Plugins**: modules, events and `huri` commands are found through entry points; huri-core depends on none of them | huri-core `test_plugins.py`, `test_core_is_abstract.py`, `test_cli.py`; the client-side files never import the server stack, even inside a function: `test_packaging.py`; the public surface does not change unnoticed: `test_public_api.py` |
| 4 | **The voice pipeline**: audio → VAD → STT → TAG → EMO/EAG → RAG/QAG → TTS → audio, plus MOV for motion, for every module set the installer accepts | huri-modules `packages/*/tests`, `tests/integration/` (`test_full_pipeline.py`, `test_module_sets.py`, `test_qag_modes.py`) |
| 5 | **Wire formats** of the `huri.*` topics, which deployed clients and robots speak | huri-modules `packages/huri-events/tests/test_wire.py` |
| 6 | **Install**: the installer resolves the pinned versions, never prompts for credentials and produces a running server | huri-modules `test_installer.py`, `test_core_pins.py`, `test_run_scripts.py`; the nightly `install.yml` |

A change to one of these needs a test in the same pull request, and a
reviewer checks that it is there.

## Strategy

### Three levels

| Level | What it runs | Fakes | Where |
|---|---|---|---|
| **Unit** | one module or one core component | the model deployments (`huri_core.testing.LocalHandle`), the network, the audio device | huri-core `tests/core/`, huri-modules `packages/*/tests` |
| **Integration** | the real event bus, the real module factory and the real curated modules in one session; the real websocket endpoint with toy modules; the client SDK against a local server | the models only: no Ray cluster, no GPU | huri-modules `tests/integration/`, huri-core `test_server_session.py`, `test_client_sdk.py` |
| **End to end** | a real install, a real image or real hardware, with the real models | nothing | the workflows and checks below |

Most tests are unit tests. They run in seconds and point at the broken line.
Integration tests prove that the parts fit together. End-to-end checks are
slow, so they run less often.

`huri_core.testing` (`ModuleHarness`, `LocalHandle`) runs a module without Ray
and without its model. It is what lets a module be tested on any laptop and in
CI, and what huri-plugin-template gives each new module.

### Automatic and manual

| Check | Runs | Blocks a merge |
|---|---|---|
| Lint (black, isort, flake8, mypy), unit and integration tests with the coverage floor, build of every package | `ci.yml`, on every push to any branch and on every pull request, in both repositories | yes, on `dev` and `main` |
| Tests on Python 3.10, 3.11 and 3.12 | huri-core `ci.yml` (huri-modules tests on 3.12) | yes |
| A client install carries no server stack (no Ray, no FastAPI) | huri-core `ci.yml` | yes |
| Installer plan on Ubuntu 22.04, 24.04 and Arch | huri-modules `ci.yml` | yes |
| huri-modules against huri-core `dev` (canary) | huri-modules `ci.yml` | no: it warns, so a core change is seen before its release |
| Base Docker image built and a real session run against it | huri-modules `docker.yml`, on each pull request that touches `deploy/`, `packages/` or the pins | not a required check; a red run is fixed before merging |
| Full install on a clean Ubuntu 22.04 and 24.04, with the model downloads | huri-modules `install.yml`, every night on `main` and on demand | no; a maintainer runs it on demand before a release |
| GPU paths (CUDA, ROCm) | huri-modules `gpu.yml`, on demand only, on a self-hosted GPU runner | no |
| **Manual**: a voice conversation with huri-cli and a microphone, and a robot (HuRI-InMoov) | a maintainer, before a release that changes audio, the models or `huri-module-mov` | the release waits for it |
| **Manual**: a newcomer installs HuRI from the docs alone, on a clean machine | [docs/reproducibility/](reproducibility/README.md), with a dated report | no; each blocker found becomes a docs fix |

What stays manual needs something CI does not have: a GPU, a microphone and a
speaker, a robot, or a person who has never seen the project. `gpu.yml` has no
runner registered yet, so the GPU checks are done by hand on a team machine
for now.

## Coverage

### Measured on 2026-11-05

Branch coverage (lines and branches) of the shipped code on `dev`, tests
excluded, with `pytest-cov`:

| Repository | Tests | Time | Coverage |
|---|---|---|---|
| huri-core | 149 | 8 s | **81 %** |
| huri-modules (`packages/`) | 368 | 43 s | **78 %** |

The gaps are mostly code that needs what the tests leave out on purpose:

- **a Ray cluster**: huri-core `app.py` (the Ray Serve app builder, 0 %) and
  `launch_huri.py` (0 %). The same logic is covered without Ray by
  `test_server_session.py`;
- **a GPU**: huri-module-tts' CosyVoice handle (27 %);
- **hardware**: huri-module-mov (55 %);
- **less tested logic that should be covered**: huri-module-rag's memory
  maintenance (69 %) and `rag.py` (70 %), huri-core `user_config.py` (15 %)
  and `factory.py` (64 %).

### Targets

| Repository | Floor | Goal |
|---|---|---|
| huri-core | 80 % | 85 % |
| huri-modules | 75 % | 80 % |
| Critical path (above) | every item has a test that fails when it breaks | |

Why these numbers:

- **The floor is about today's level, rounded down.** Coverage must not drop:
  CI fails when a repository goes below its floor.
- **The goal closes the gaps that should be closed** (the RAG memory, the
  user config, the factory), which brings each repository about 5 points up.
- **100 % is not a target.** The last part is Ray, GPU, audio-device and
  hardware glue. It is checked end to end (`docker.yml`, `install.yml`, the
  manual checks), where a mocked unit test would prove little.
- **A percentage does not prove the critical path is tested.** Its table is
  the stronger rule, and a reviewer checks it.

CI measures it on every push and pull request, with `pytest-cov`, and fails
under the floor (`--cov-fail-under`): huri-core in each `pytest (python ...)`
job, huri-modules in `pytest (huri-core pinned)`, where `pyproject.toml` leaves
the tests out of the count. The report lists the uncovered lines. Locally:

```sh
pytest --cov=huri_core --cov-branch --cov-report=term-missing --cov-fail-under=80   # huri-core
pytest --cov=packages --cov-branch --cov-report=term-missing --cov-fail-under=75    # huri-modules
```

## Team conventions

- **pytest**, with `pytest-asyncio` for coroutines (`@pytest.mark.asyncio`).
  Files are `test_*.py` under `tests/`; in huri-modules each package keeps its
  own `packages/<package>/tests/`.
- **Each test file opens with a docstring** that says what it protects, for
  example: "Modules that can move hardware need an explicit, separate opt-in."
- **A test names the behaviour**, not the function: `test_same_phrase_can_be_spoken_twice`.
- **A bug fix comes with a test that fails without the fix.** A bug that
  happened once stays pinned by a test (`test_merge_regressions.py`: "What TAG
  must never do to a sentence").
- **A new feature comes with its tests** in the same pull request; a new module
  comes with unit tests through `huri_core.testing`.
- **Tests are hermetic**: no network (URLs are faked, `git` never prompts), no
  model download (`LocalHandle` stands in for the deployment), no audio device,
  no GPU, no Ray cluster. A test that needs one of them skips with a reason
  when it is missing (`pytest.importorskip("ray.serve", reason=...)`), or
  reads its resource from an environment variable (`HURI_PIPER_VOICE`).
- **Tests are fast**: a repository's whole suite runs in under a minute on a
  laptop, so everyone runs it before pushing (`make check` in huri-core and
  huri-modules).
- **Code runs on Python 3.10 to 3.12**, and CI tests each one.
- **Nobody merges on red.** The required checks block a merge on `dev` and
  `main`, and a merge needs a maintainer's approval other than the author's.
  A flaky test is fixed or removed, never retried until it passes.
