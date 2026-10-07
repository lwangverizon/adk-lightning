#!/usr/bin/env bash
# Run from repository root. No model calls, host imports or container publication.
set -euo pipefail
if [[ $# != 2 ]]; then
  echo 'Usage: bash scripts/build_portal.sh REVIEWED_REGISTRY.json NEW_OUTPUT_DIR' >&2
  exit 2
fi
registry="$1"
out="$2"
if [[ -e "$out" ]]; then echo 'Output exists; choose a new version directory' >&2; exit 2; fi
ui_build="$(uv run --no-sync python scripts/portal_build_identity.py)"
uv run --no-sync python -m adkl.portal assemble "$registry" --out "$out/web/data" --approve-content --ui-build "$ui_build"
# The bootstrap only changes the static portal build, never the normal editor.
bootstrap=console/web/flutter_bootstrap.js
if [[ -e "$bootstrap" ]]; then echo 'Unexpected bootstrap; refusing to overwrite' >&2; exit 2; fi
trap 'rm -f "$bootstrap"' EXIT
cp deploy/portal/flutter_bootstrap.js "$bootstrap"
(cd console && flutter pub get --enforce-lockfile && flutter build web --release --target lib/portal_main.dart --dart-define="ADKL_PORTAL_BUILD=$ui_build" --no-web-resources-cdn)
rm "$bootstrap"
trap - EXIT
cp -R console/build/web/. "$out/web/"
cp -R deploy/portal/fonts "$out/web/fonts"
cp deploy/portal/{Dockerfile,nginx.conf} "$out/"
uv run --no-sync python scripts/ci/artifact_manifest.py create "$out/web" "$out/manifest.json"
uv run --no-sync python scripts/ci/artifact_manifest.py verify "$out/web" "$out/manifest.json"
echo "Prepared static portal context: $out (not a published or certified container)"
