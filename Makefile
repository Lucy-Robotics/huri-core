SRC := huri_core scripts tests

# Run every check even if an earlier one fails, then fail at the end if any did.
# black and isort rewrite the files; CI runs them with --check.
lint:
	@rc=0; \
	for cmd in "black $(SRC)" "isort $(SRC)" "flake8 $(SRC)" "mypy huri_core scripts --check-untyped-defs" \
		"python scripts/check_links.py"; do \
		echo "==> $$cmd"; \
		$$cmd || rc=1; \
	done; \
	exit $$rc

test:
	pytest tests

# Run lint and test even if lint fails, then fail at the end if either did.
check:
	@rc=0; \
	$(MAKE) --no-print-directory lint || rc=1; \
	$(MAKE) --no-print-directory test || rc=1; \
	exit $$rc

bench:
	pytest tests/benchmarks --benchmark-only --benchmark-columns=mean,median,rounds

# Benchmarks against another branch (BASE); SUMMARY is a file to append the table to;
# RELEASE (a tag) is also compared, report only.
BASE ?= origin/dev
bench-compare:
	scripts/bench_compare.sh "$(BASE)" "$(SUMMARY)" "$(RELEASE)"

.PHONY: lint test check bench bench-compare
