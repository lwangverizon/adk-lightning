"""Bundle Flutter's licensed default fonts into a connected web build.

No downloads or host agent imports. Run after flutter build web and before
artifact verification/publication; never mutate a deployed/read-only directory.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import shutil


def prepare(web: Path, font_directory: Path) -> None:
    manifest = web / "assets/FontManifest.json"
    bootstrap = web / "flutter_bootstrap.js"
    loader = bootstrap.read_text(encoding="utf-8")
    original = "_flutter.loader.load();"
    configured = ("_flutter.loader.load({config: {fontFallbackBaseUrl: "
                  "new URL('fonts/', document.baseURI).href}});")
    if original not in loader and configured not in loader:
        raise ValueError("Unsupported Flutter bootstrap; no build modified")
    entries = json.loads(manifest.read_text(encoding="utf-8"))
    sources = [font_directory / name for name in (
        "Roboto-Regular.ttf", "Roboto-Medium.ttf", "Roboto-Bold.ttf", "Roboto_LICENSE.txt")]
    if not all(path.is_file() for path in sources):
        raise ValueError("Flutter material font files and license are required")
    target = web / "assets/fonts"
    target.mkdir(parents=True, exist_ok=True)
    for source in sources:
        shutil.copyfile(source, target / source.name)
    entries = [entry for entry in entries if entry["family"] != "Roboto"]
    entries.append({"family": "Roboto", "fonts": [
        {"asset": "fonts/Roboto-Regular.ttf", "weight": 400},
        {"asset": "fonts/Roboto-Medium.ttf", "weight": 500},
        {"asset": "fonts/Roboto-Bold.ttf", "weight": 700},
    ]})
    manifest.write_text(json.dumps(entries), encoding="utf-8")
    fallback = Path(__file__).resolve().parents[1] / "deploy/portal/fonts"
    shutil.copytree(fallback / "notosanssymbols", web / "fonts/notosanssymbols", dirs_exist_ok=True)
    shutil.copyfile(fallback / "NotoSansSymbols-OFL.txt", web / "fonts/NotoSansSymbols-OFL.txt")
    bootstrap.write_text(loader.replace(original, configured), encoding="utf-8")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--web", type=Path, required=True)
    parser.add_argument("--font-directory", type=Path, required=True,
                        help="FLUTTER_ROOT/bin/cache/artifacts/material_fonts")
    args = parser.parse_args()
    prepare(args.web, args.font_directory)


if __name__ == "__main__":
    main()
