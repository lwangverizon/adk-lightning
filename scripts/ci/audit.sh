#!/usr/bin/env bash
# Public advisory queries only. Run after uv sync --locked --only-group ci.
set -euo pipefail
cd "$(dirname "$0")/../.."
mkdir -p reports
uv export --locked --extra runtime --no-default-groups --no-emit-project \
  --format requirements.txt --output-file reports/runtime-requirements.txt >/dev/null
uv export --locked --extra runtime --no-default-groups --no-emit-project \
  --format cyclonedx1.5 --output-file reports/runtime-sbom.cdx.json >/dev/null
# Fully pinned/hash-checked input avoids resolving or installing audited packages.
# Nonzero on findings OR service failure. No blanket ignore list.
uv run --no-sync pip-audit --disable-pip --require-hashes \
  --progress-spinner off -r reports/runtime-requirements.txt \
  --format json --output reports/dependency-audit.json
