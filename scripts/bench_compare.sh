#!/usr/bin/env bash
# Benchmarks of this tree against BASE (default origin/dev), same benchmark code on both.
# Usage: scripts/bench_compare.sh [BASE] [SUMMARY_FILE]   (run from the repo root)
# Exits 1 when a benchmark's mean got more than 25% slower than on BASE.
set -uo pipefail

base="${1:-origin/dev}"
summary="${2:-}"
cols=mean,median,rounds

tmp="$(mktemp -d)"
# The store lives under the repo: pytest-benchmark fails to compare from a store outside the cwd.
store_dir="$PWD/.benchmarks/compare-$$"
trap 'git worktree remove --force "$tmp/base" 2>/dev/null; rm -rf "$tmp" "$store_dir"' EXIT
store="file://$store_dir"

git worktree add --detach "$tmp/base" "$base" || exit 1
# The base may have no benchmarks (first PR): measure both trees with this tree's.
rm -rf "$tmp/base/tests/benchmarks"
cp -r tests/benchmarks "$tmp/base/tests/benchmarks"

# PYTHONPATH guarantees the base run imports the base's huri_core (checked: pytest already resolves it there), not the editable install of this tree.
(cd "$tmp/base" && PYTHONPATH="$tmp/base" pytest tests/benchmarks --benchmark-only \
  --benchmark-save=base --benchmark-storage="$store") || {
  [[ -n "$summary" ]] && echo "Benchmarks: base run failed (the base may lack an API a newer benchmark uses), see the job log." >> "$summary"
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
exit "$rc"
