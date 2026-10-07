#!/usr/bin/env bash
# Exercise browser codecs without depending on a manual user gesture in headless CI.
set -euo pipefail
adkl_repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
adkl_browser_path="${CHROME_EXECUTABLE:-}"
if [[ -z "$adkl_browser_path" ]]; then
  if [[ -x '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome' ]]; then
    adkl_browser_path='/Applications/Google Chrome.app/Contents/MacOS/Google Chrome'
  else
    adkl_browser_path="$(command -v google-chrome || command -v chromium || command -v chromium-browser || true)"
  fi
fi
if [[ ! -x "$adkl_browser_path" ]]; then
  echo 'Chrome is required; set CHROME_EXECUTABLE to its executable.' >&2
  exit 1
fi
adkl_browser_wrapper="$(mktemp "${TMPDIR:-/tmp}/adkl-media-chrome.XXXXXX")"
trap 'rm -f "$adkl_browser_wrapper"' EXIT
cat > "$adkl_browser_wrapper" <<'WRAPPER'
#!/usr/bin/env bash
exec "$ADKL_TEST_CHROME" --autoplay-policy=no-user-gesture-required "$@"
WRAPPER
chmod 700 "$adkl_browser_wrapper"
cd "$adkl_repo_root/console"
ADKL_TEST_CHROME="$adkl_browser_path" CHROME_EXECUTABLE="$adkl_browser_wrapper" \
  flutter test --no-pub --platform chrome test/browser/a2ui_media_test.dart
