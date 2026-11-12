# Installing HuRI

This page covers the whole install: what to have first, the steps for each way
of using HuRI, the configuration files, the environment variables, and how to
check that it runs. The details live in each repository's README; this page
links to them.

| You want to… | Path | Needs a GPU, models, a running pipeline? |
|---|---|---|
| Run the pipeline: voice in, an answer out as text, voice and gestures | [1. The pipeline](#1-the-pipeline-huri-modules-installer) | a GPU is optional; models yes |
| Use the framework in your own code | [2. The framework](#2-the-framework-huri-core) | no |
| Write a module | [3. A module of your own](#3-a-module-of-your-own) | no |
| Write a client, or use the reference one | [4. A client](#4-a-client) | a running server to talk to |

## Prerequisites

| | Requirement |
|---|---|
| OS | Linux. CI checks the installer's plan on Ubuntu 22.04, Ubuntu 24.04 and Arch Linux, and runs a full install on both Ubuntu versions on demand. The installer knows `apt`, `pacman`, `dnf` and `zypper`. Paths 2 to 4 are plain Python and need no particular distribution. |
| Python | 3.10, 3.11 or 3.12 (3.13 is not supported yet), in a virtualenv made with `venv` or [`uv`](#with-uv) |
| Tools | `git`; `make` to run `make check`; `sudo` for the installer, which installs the system packages itself; `zstd` for Ollama's installer, which HuRI's installer does not install yet |
| Audio | PortAudio (`libportaudio2` on Debian/Ubuntu, `portaudio` on Arch/Fedora) for huri-cli to open the microphone and the speakers |
| Disk, RAM | path 1 only: about 8 GiB of disk for the default pipeline (20 GiB with CosyVoice), about 16 GiB of RAM; the installer refuses a plan that does not fit |
| GPU | optional. Without one, the pipeline still has voice in and voice out (faster-whisper on CPU, Piper TTS). An NVIDIA GPU adds CosyVoice TTS and gesture generation ([what runs where](https://github.com/Lucy-Robotics/huri-modules#what-runs-where)) |
| Network | github.com (HuRI is installed from git), pypi.org and huggingface.co (the models; the emotion model comes at the first start). For the LLM, ollama.com and registry.ollama.ai with Ollama; with [llama.cpp](#using-llamacpp-instead-of-ollama), huggingface.co for the GGUF model and ghcr.io if you use its Docker image. [After the install](#without-a-network), no network is needed |

On Ubuntu 24.04:

```sh
sudo apt install python3.12 python3.12-venv python3.12-dev python3-pip git make libportaudio2 zstd
```

Ubuntu 22.04's Python is 3.10, which is supported: install `python3
python3-venv python3-dev` instead. On Arch, `python` is newer than 3.12:
install 3.12 next to it and give it to the installer with `--python` (see
below).

### With uv

[uv](https://docs.astral.sh/uv/) works everywhere this page uses `venv` and
`pip`. It also installs a supported Python next to a newer system one:

```sh
uv python install 3.12
uv venv --python 3.12 .venv && . .venv/bin/activate
uv pip install ...                                 # where this page says pip install
./install.sh --python "$(uv python find 3.12)"     # the pipeline's installer
```

A virtualenv made by uv has no `pip`, so `pip install` would find another one
or fail: use `uv pip install`. The `install.sh` of a module made from the
template detects uv by itself.

In zsh, run `rehash` after installing a tool into an active virtualenv. Without
it, `pytest` can still start from your system Python and fail on missing
imports.

## 1. The pipeline (huri-modules installer)

```sh
git clone https://github.com/Lucy-Robotics/huri-modules.git && cd huri-modules
./install.sh --plan-only      # what would run on this machine, and why
./install.sh                  # show the plan, then install it
.huri-local/start.sh          # start Qdrant, Ollama, Ray and the HuRI application
```

The installer probes the GPU, RAM, disk and Python. It then installs the system
packages, a virtualenv with the right torch build, huri-core and huri-cli at
the refs in [`versions.env`](https://github.com/Lucy-Robotics/huri-modules/blob/main/versions.env),
the modules that fit, their models, Qdrant and Ollama. It writes the
configuration files listed [below](#configuration). `./install.sh --help` lists
every flag, for example:

- `--modules vad,stt,tag,qag,rag` to install only some modules;
- `--llm-provider`, `--llm-url` and `--llm-model` for a remote LLM;
- `--no-cli` for a server only.

[Choosing what to run](https://github.com/Lucy-Robotics/huri-modules#choosing-what-to-run)
explains them.

Talk to it from a second terminal:

```sh
source .huri-local/env.sh
huri-cli --config config/client_local.generated.yaml
```

You get a `>>` prompt. Type a sentence, or speak if the plan has speech in. The
answer is printed and, when the plan has TTS, spoken. `\exit` quits the client
and leaves the server running.

```sh
.huri-local/status.sh         # what is up: the application status, not only the HTTP proxy
.huri-local/stop.sh --all     # stop everything start.sh started
```

The whole log of the install is in `.huri-local/install.log`. `./install.sh
--only <stage>` re-runs one stage, for example `--only config` to regenerate the
configuration. Docker images and a Helm chart are the other ways to deploy:
[Docker images](https://github.com/Lucy-Robotics/huri-modules#docker-images).

### Using llama.cpp instead of Ollama

The installer sets up Ollama, but the LLM can be anything that speaks the
OpenAI API. For a local [llama.cpp](https://github.com/ggml-org/llama.cpp)
server, start it, point HuRI at it, then restart:

```sh
llama-server -hf Qwen/Qwen2.5-1.5B-Instruct-GGUF:Q4_K_M --port 8080 -c 8192
./install.sh --only config --llm-url http://localhost:8080 --llm-provider vllm \
  --llm-model "Qwen/Qwen2.5-1.5B-Instruct-GGUF:Q4_K_M" \
  --embed-url http://localhost:11434 --embed-model bge-m3
.huri-local/stop.sh --all && .huri-local/start.sh
```

- `llama-server` also comes as the Docker image
  `ghcr.io/ggml-org/llama.cpp:server`.
- `--embed-url` keeps the embeddings on Ollama. Change it only if your server
  also serves the embedding model.
- `--only config` plans again: pass the flags of your install too, such as
  `--force-gesture`. The plan it prints still says `ollama` on the LLM line; the
  generated config has the right provider.
- A server started with `--api-key` needs `--llm-provider api --llm-api-key
  <key>`. The key goes to `.huri-local/secrets.env`, not to the YAML files.

### Without a network

Once the first `start.sh` has downloaded the emotion model, the whole pipeline
runs with no network: speech, answers, memory, documents and gestures. A
conversation leaves the machine only if you point HuRI at a remote LLM or a
remote Qdrant.

With a network, the libraries still contact outside services when the server
starts: huggingface.co, Ray's usage statistics (`usage-stats.ray.io`) and a
Microsoft telemetry endpoint (`mobile.events.data.microsoft.com`). No new
connection was opened during conversations. Setting
`RAY_USAGE_STATS_ENABLED=0` in the shell before `start.sh` did not stop the
lookup of Ray's address.

## 2. The framework (huri-core)

huri-core is on [PyPI](https://pypi.org/project/huri-core/) and tagged in git
(`X.Y.Z`). In a virtualenv:

```sh
pip install "huri-core[server]"   # a server: Ray Serve and FastAPI
pip install "huri-core[client]"   # a client: the SDK, without Ray or FastAPI
pip install huri-core             # the base classes and huri_core.testing, no dependency
```

To work on huri-core itself:

```sh
git clone https://github.com/Lucy-Robotics/huri-core.git && cd huri-core
python3 -m venv .venv && . .venv/bin/activate
pip install -c constraints.txt -e ".[server,client,dev]"
make check                        # lint, link check and the tests, as CI runs them
```

`constraints.txt` holds the pins the server needs (protobuf, FastAPI,
setuptools). Pass it with `-c` to anything you install afterwards into a
server environment, so that a module's dependencies cannot move them.

The [minimal end to end example](../README.md#a-minimal-end-to-end-example) is
the shortest way to see a server and a client work: a module of a few lines,
a server running it, and a client that sends `hello` and prints `HELLO!`.

### Check the status of a server

From a shell with the server's virtualenv active:

| Command | What it says |
|---|---|
| `serve status` | each application and deployment: `RUNNING`, `HEALTHY`, or the error |
| `ray status` | the Ray nodes and the CPUs and GPUs in use |
| `huri --help` | the `huri` commands the installed packages provide |

And local websites, on the machine that runs the server:

| Address | What it shows |
|---|---|
| <http://127.0.0.1:8265> | Ray's dashboard: deployments, replicas, logs, CPU and memory |
| <http://127.0.0.1:8000/docs> | the server's HTTP API, to read and try |
| <http://127.0.0.1:8000/modules> | the modules this server enabled, with their input and output topics (JSON) |
| <http://127.0.0.1:8000/-/healthz> | Ray's HTTP proxy only: it answers `success` even when every deployment is dead, so trust `serve status` instead |

The pipeline of path 1 adds:

| Command or address | What it shows |
|---|---|
| `.huri-local/status.sh` | Qdrant, the LLM, Ray and the HuRI application: `up` or `down` |
| <http://127.0.0.1:6333/collections> | Qdrant's collections: `conversations`, `documents` (JSON) |
| <http://127.0.0.1:6333/dashboard> | Qdrant's dashboard, when Qdrant runs in Docker. The standalone binary the installer uses without Docker has none (404) |
| <http://127.0.0.1:11434/api/ps> | the models Ollama has loaded (JSON) |
| <http://127.0.0.1:8080/health> | a llama.cpp server, if you use one |

To talk to HuRI from a browser, `huri-playground` (see
[Built with HuRI](../README.md#built-with-huri)) is a local website too. It
listens on port 8080, so change the port when llama.cpp already uses it:

```sh
PLAYGROUND_PORT=8090 HURI_URL=ws://localhost:8000/session huri-playground    # http://127.0.0.1:8090
```

## 3. A module of your own

Start from [huri-plugin-template](https://github.com/Lucy-Robotics/huri-plugin-template):
**Use this template**, clone the new repository, then:

```sh
./rename.sh servo                 # your module's name, once, on the fresh clone
python -m venv .venv && . .venv/bin/activate
python -m pip install \
  "huri-core[server] @ git+https://github.com/Lucy-Robotics/huri-core.git@0.4.0" \
  "huri-events @ git+https://github.com/Lucy-Robotics/huri-modules.git@0.1.0#subdirectory=packages/huri-events"
./install.sh                      # the module's pinned dependencies for this machine
pytest                            # runs the module with huri_core.testing: no Ray, no GPU
```

`0.4.0` and `0.1.0` are the current pair. Pick the huri-core and
huri-modules refs together from the
[compatibility table](../README.md#versioning-and-compatibility). The template's
README explains the rest: the manifest, the events package and how to get the
module listed.

## 4. A client

The installer of path 1 already installs huri-cli. To install it on its own,
in a virtualenv, at refs from
[its compatibility table](https://github.com/Lucy-Robotics/HuRI-CLI#versions-and-compatibility):

```sh
pip install \
  "huri-core[client] @ git+https://github.com/Lucy-Robotics/huri-core.git@<core-ref>" \
  "huri-events @ git+https://github.com/Lucy-Robotics/huri-modules.git@<modules-ref>#subdirectory=packages/huri-events" \
  "huri-cli @ git+https://github.com/Lucy-Robotics/HuRI-CLI.git@<cli-ref>"
huri-cli --config client.yaml
```

`client.yaml` describes the session: the server URL, the modules and the
topics. Start from one of HuRI-CLI's
[config files](https://github.com/Lucy-Robotics/HuRI-CLI#config-files), or use
the one the installer of path 1 generated.

To write your own client, start from the [client SDK](../README.md#client-sdk).

## Configuration

The installer of path 1 writes every file below. A server you start yourself
(path 2) needs none of them: it reads only the environment variables below.

| File | Used by | What it sets |
|---|---|---|
| `config/huri_local.generated.yaml` | `serve deploy` (run by `start.sh`) | the Ray Serve application: one deployment per module, its replicas, CPUs and GPUs, its options (LLM URL and model, Qdrant URL, ...), and the environment variables below in `runtime_env.env_vars` |
| `config/client_local.generated.yaml` | huri-cli | the session: the server URL (`huri_url`), the modules and their arguments, the topics the client sends and receives, plus huri-cli's `cli:` section (audio devices, saving audio and motion) |
| `.huri-local/huri.env` | `env.sh` | the same runtime environment variables, as a plain `.env` |
| `.huri-local/secrets.env` | `env.sh` | the LLM API key (`0600`, never written into the YAML files) |
| `.huri-local/plan.env` | the installer | the hardware plan it acted on |
| `~/.config/huri/_user_id` | the client SDK | the user ID a client reuses from one session to the next (under `$XDG_CONFIG_HOME/huri` when it is set; `user_id_file_path` in the client config moves it) |

The modules a client asks for must be ones the server enabled. Otherwise the
session is refused with a `session_error` that lists both sets.

## Environment variables

None of them is needed for paths 2 to 4, except `HURI_MODULES` to run a server.
On the pipeline, the installer sets them in the generated Serve config. Set
them yourself only to override it, or when you write the config by hand.

**Server (huri-core)**

| Variable | Default | Effect |
|---|---|---|
| `HURI_EVENT_POLICY` | `interrupt` | the event policy class: `interrupt`, `concurrent` (no interrupt rules accepted), an entry point name from the `huri.event_policies` group, or an import path. An unknown name stops the server. See [Interruptions](../README.md#interruptions-the-event-policy) |
| `HURI_MODULES` | unset: no module is enabled, every session is refused | comma-separated allow-list of the modules this server registers: entry point names (`stt,rag,tts`) or import paths (`my_pkg.servo:Servo`) |
| `HURI_ACTUATION_MODULES` | unset | the modules allowed to move hardware. A module that declares actuation must be listed here as well as in `HURI_MODULES`. Any client that can reach `/session` can then use it: keep the server on a private network or behind an authenticating proxy |

**Installer (huri-modules `install.sh`)**

| Variable | Default | Effect |
|---|---|---|
| `HURI_CORE_REF` / `HURI_CLI_REF` | the value in `versions.env` | the git ref of huri-core / huri-cli to install |
| `HURI_CORE_PATH` / `HURI_CLI_PATH` | unset | install that local checkout, editable, instead of the ref |

**Modules (set by the installer in the Serve config)**

| Variable | Module | Default | Effect |
|---|---|---|---|
| `HURI_STT_MODEL_PATH` | stt | `base` | faster-whisper model name or path |
| `HURI_STT_DEVICE` | stt | `auto` | `cpu`, `cuda` or `auto` |
| `HURI_STT_COMPUTE_TYPE` | stt | `auto` | `int8`, `float16` or `auto` |
| `HURI_STT_NUM_WORKERS` | stt | `2` | faster-whisper workers |
| `HURI_PIPER_VOICE` | tts (Piper) | `assets/models/piper/en_US-lessac-medium.onnx` | Piper voice file |
| `HURI_MODEL_PATH` | tts (CosyVoice) | a path under `/models/cosytts/` | CosyVoice model directory |
| `HURI_COSY_DIR` | tts (CosyVoice) | unset | CosyVoice source checkout, added to the import path |
| `HURI_VOICE_SAMPLE_PATH` | tts (CosyVoice) | `/assets/voice.wav` | the reference voice to clone |
| `HURI_VOICE_TRANSCRIPT` | tts (CosyVoice) | unset: the deployment fails | what is said in the reference voice sample |
| `HURI_TTS_FP16` | tts (CosyVoice) | on when CUDA is available | `1`/`0`: fp16 inference |
| `HURI_TTS_TRT` | tts (CosyVoice) | off | `1`: TensorRT for the flow estimator (builds an engine at startup) |
| `HURI_EMO_MODEL` | emo | `superb/hubert-large-superb-er` | Hugging Face model for speech emotion |
| `HURI_EMAGE_REPO` | mov | `H-Liu1997/emage_audio` | EMAGE model repository or path |
| `HURI_GESTURE_CONTEXT_SEC` | mov | `2.0` | audio context of each gesture window, in seconds |
| `HURI_GESTURE_MIN_CHUNK_SEC` | mov | `0.5` | shortest audio chunk turned into gestures, in seconds |
| `HURI_GESTURE_BLEND_SEC` | mov | `0.2` | blend between two windows, in seconds |
| `HURI_GESTURE_GPU_MEM_FRACTION` | mov | `0.0` (no cap) | share of the GPU memory the gesture process may use, on CUDA |
| `HURI_GESTURE_WARMUP_SR` | mov | `24000` | sample rate used to warm the model up |
| `HURI_LLM_API_KEY` | rag | empty | API key of a hosted LLM (`--llm-api-key` stores it in `.huri-local/secrets.env`) |
| `HURI_RAG_DEFAULT_PERSONA` | rag | the Mouse-Man persona | the system prompt used when the session gives none |
| `HSA_OVERRIDE_GFX_VERSION` | all, on AMD | set by `--only config` when needed | lets ROCm run on a GPU of the same family as a supported one |

**`huri hub`**

| Variable | Default | Effect |
|---|---|---|
| `HURI_REGISTRY` | the huri-modules checkout you are in, else huri-modules on GitHub | path or URL of the `registry.toml` to read |
| `HURI_MODULES_REF` | `main` | the huri-modules ref whose registry is read from GitHub |
| `GITHUB_TOKEN` | unset | a token for GitHub's API and private repositories |

**Development**

| Variable | Default | Effect |
|---|---|---|
| `HURI_SPLIT_DIR` | unset | a directory with checkouts of the other repositories, for `scripts/check_links.py --siblings` |

## Check that it works

| Path | Command | Expected |
|---|---|---|
| 1 | `.huri-local/status.sh` | the `huri-app` application is `RUNNING` |
| 1 | `huri-cli --config config/client_local.generated.yaml`, then type `hello` | an answer, printed, and spoken when the plan has TTS |
| 1 | `make check` in huri-modules | lint and tests pass (no GPU, no model needed) |
| 2 | `make check` in huri-core | lint, link check and tests pass |
| 2 | the [minimal end to end example](../README.md#a-minimal-end-to-end-example) | the client prints `HELLO!` |
| 3 | `pytest` in the module | the template's tests pass |
| 4 | `huri-cli --help` | the usage; `pytest -q` in a HuRI-CLI checkout passes too |

When something fails, see
[Troubleshooting](https://github.com/Lucy-Robotics/huri-modules#troubleshooting)
for the pipeline, and open an issue (see [CONTRIBUTING.md](../CONTRIBUTING.md))
with the command, its output and `.huri-local/install.log`.

Whether someone outside the team can follow this page on a clean machine is
tested regularly: [the protocol and the reports](reproducibility/README.md).
