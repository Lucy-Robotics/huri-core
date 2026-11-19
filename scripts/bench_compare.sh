#!/usr/bin/env bash
# Benchmarks of this tree against BASE (default origin/dev), same benchmark code on both.
# Usage: scripts/bench_compare.sh [BASE] [SUMMARY_FILE] [RELEASE]   (run from the repo root)
# Exits 1 when a benchmark's mean got more than 25% slower than on BASE.
# RELEASE (e.g. the last release tag): also compared, report only. Against BASE a
# pull request sees its own change; against RELEASE, all the cost merged since.
set -uo pipefail

base="${1:-origin/dev}"
summary="${2:-}"
release="${3:-}"
cols=mean,median,rounds

tmp="$(mktemp -d)"
# The store lives under the repo: pytest-benchmark fails to compare from a store outside the cwd.
store_dir="$PWD/.benchmarks/compare-$$"
trap 'for w in "$tmp"/*/; do git worktree remove --force "$w" 2>/dev/null; done; rm -rf "$tmp" "$store_dir"' EXIT
store="file://$store_dir"

note() { [[ -n "$summary" ]] && echo "$1" >> "$summary"; echo "$1"; }

# measure REF NAME: this tree's benchmarks on REF's huri_core, saved as NAME.
measure() {
  local dir="$tmp/$2"
  git worktree add --detach "$dir" "$1" || return 1
  # REF may have no benchmarks (first PR, an older release): measure with this tree's.
  rm -rf "$dir/tests/benchmarks"
  cp -r tests/benchmarks "$dir/tests/benchmarks"
  # PYTHONPATH guarantees the run imports REF's huri_core (checked: pytest already resolves it there), not the editable install of this tree.
  (cd "$dir" && PYTHONPATH="$dir" pytest tests/benchmarks --benchmark-only \
    --benchmark-save="$2" --benchmark-storage="$store")
}

measure "$base" base || {
  note "Benchmarks: base run failed (the base may lack an API a newer benchmark uses), see the job log."
  exit 1
}

pytest tests/benchmarks --benchmark-only --benchmark-save=pr --benchmark-storage="$store" \
  --benchmark-compare=0001 --benchmark-compare-fail=mean:25% --benchmark-columns="$cols"
rc=$?

table="$(pytest-benchmark --storage "$store" compare 0001 0002 --group-by=name --columns="$cols")"
echo "$table"
if [[ -n "$summary" ]]; then
  printf '### Benchmarks: base (0001) vs this pull request (0002)\n```\n%s\n```\n' "$table" >> "$summary"
fi

# Report only: never changes the exit code.
if [[ -n "$release" && "$(git rev-parse "$release^{commit}")" != "$(git rev-parse "$base^{commit}")" ]]; then
  if measure "$release" release; then
    table="$(pytest-benchmark --storage "$store" compare 0003 0002 --group-by=name --columns="$cols")"
    echo "$table"
    if [[ -n "$summary" ]]; then
      printf '### Report only: last release %s (0003) vs this pull request (0002)\n```\n%s\n```\n' "$release" "$table" >> "$summary"
    fi
  else
    note "Benchmarks: the run on release $release failed (it may lack an API a newer benchmark uses); report only."
  fi
fi
exit "$rc"
