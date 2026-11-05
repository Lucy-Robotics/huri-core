#!/usr/bin/env bash
set -uo pipefail

if [[ "${1:-}" != --inside ]]; then
  out="$(readlink -f "${1:-reproducibility-out}")"
  mkdir -p "$out"
  exec docker run --rm --shm-size=2g \
    -v "$(readlink -f "$0"):/dry_run.sh:ro" -v "$out:/out" \
    ubuntu:24.04 bash /dry_run.sh --inside
fi

OUT=/out
CORE_TAG=0.4.0
MODULES_TAG=0.1.0
GH=https://github.com/Lucy-Robotics
export PIP_DISABLE_PIP_VERSION_CHECK=1 GIT_TERMINAL_PROMPT=0 DEBIAN_FRONTEND=noninteractive
: > "$OUT/summary.tsv"

step() {
  local id="$1" title="$2"; shift 2
  local t0=$SECONDS rc
  echo "=== $id $title"
  ( set -e; "$@" ) > "$OUT/$id.log" 2>&1
  rc=$?
  printf '%s\t%s\t%s\t%ss\n' "$id" "$title" "$([ $rc -eq 0 ] && echo PASS || echo "FAIL($rc)")" \
    "$((SECONDS - t0))" | tee -a "$OUT/summary.tsv"
}

s0_system() {
  apt-get update -qq
  apt-get install -y -qq python3.12 python3.12-venv python3.12-dev python3-pip git make libportaudio2 curl >/dev/null
  python3.12 --version; git --version; make --version | head -1
}

s1_core() {
  cd /work && git clone -q --branch dev "$GH/huri-core.git" && cd huri-core
  git rev-parse HEAD
  python3.12 -m venv .venv && export PATH="$PWD/.venv/bin:$PATH"
  pip install -q -c constraints.txt -e ".[server,client,dev]"
  make check
}

s2_example() {
  mkdir -p /work/example/my-echo/my_echo && cd /work/example
  python3.12 -m venv .venv && export PATH="$PWD/.venv/bin:$PATH"
  cat > my-echo/pyproject.toml <<'T'
[project]
name = "my-echo"
version = "0.1.0"
dependencies = ["huri-core>=0.4,<0.5"]

[project.entry-points."huri.modules"]
shout = "my_echo:Shout"

[project.entry-points."huri.events"]
"echo.text" = "my_echo:Text"
"echo.loud" = "my_echo:Text"
T
  cat > my-echo/my_echo/__init__.py <<'T'
from dataclasses import dataclass

from huri_core.core.events import JsonEvent
from huri_core.core.module import Module


@dataclass
class Text(JsonEvent):
    text: str


class Shout(Module):
    input_type, output_type = "echo.text", "echo.loud"
    input_event, output_event = Text, Text

    async def process(self, data: Text):
        return Text(data.text.upper() + "!")
T
  cat > client.py <<'T'
import asyncio

from huri_core.core.client import HuRIClient
from my_echo import Text


async def main() -> None:
    client = HuRIClient(
        "ws://localhost:8000/session",
        modules={"shout": {"name": "shout"}},
        sends=["echo.text"],
    )
    done = asyncio.Event()

    async def show(event: Text) -> None:
        print(event.text)
        done.set()

    client.on("echo.loud", Text, show)
    async with client:
        await client.send("echo.text", Text("hello"))
        await asyncio.wait_for(done.wait(), 10)


asyncio.run(main())
T
  pip install -q "huri-core[server,client] @ git+$GH/huri-core.git@$CORE_TAG"
  pip install -q -e ./my-echo
  (HURI_MODULES=shout python -m huri_core.launch_huri > server.log 2>&1 &)
  for _ in $(seq 120); do
    python -c "import socket; socket.create_connection(('localhost', 8000), 1)" 2>/dev/null && break
    sleep 2
  done
  sleep 5
  python client.py | tee client.out
  grep -qx 'HELLO!' client.out
}

s3_template() {
  cd /work && git clone -q --branch dev "$GH/huri-plugin-template.git" huri-module-servo && cd huri-module-servo
  git rev-parse HEAD
  ./rename.sh servo
  python3.12 -m venv .venv && export PATH="$PWD/.venv/bin:$PATH"
  python -m pip install -q \
    "huri-core[server] @ git+$GH/huri-core.git@$CORE_TAG" \
    "huri-events @ git+$GH/huri-modules.git@$MODULES_TAG#subdirectory=packages/huri-events"
  ./install.sh
  pytest
}

s4_cli() {
  cd /work && git clone -q --branch dev "$GH/HuRI-CLI.git" && cd HuRI-CLI
  git rev-parse HEAD
  python3.12 -m venv .venv && export PATH="$PWD/.venv/bin:$PATH"
  pip install -q \
    "huri-core[client] @ git+$GH/huri-core.git@$CORE_TAG" \
    "huri-events @ git+$GH/huri-modules.git@dev#subdirectory=packages/huri-events"
  pip install -q -e ".[dev]"
  pytest -q
  huri-cli --help | head -5
}

s5_modules_plan() {
  cd /work && git clone -q --branch dev "$GH/huri-modules.git" && cd huri-modules
  git rev-parse HEAD
  ./install.sh --plan-only
}

mkdir -p /work
step S0 "system packages (Ubuntu 24.04)" s0_system
step S1 "huri-core: install for development + make check" s1_core
step S2 "README minimal end-to-end example (server + client)" s2_example
step S3 "module from huri-plugin-template: rename, install, pytest" s3_template
step S4 "HuRI-CLI: install for development + pytest" s4_cli
step S5 "huri-modules: install.sh --plan-only" s5_modules_plan
! grep -q FAIL "$OUT/summary.tsv"
