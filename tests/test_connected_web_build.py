"""Build preparation is offline, preserves font licenses and survives reruns."""
import json
from pathlib import Path

import pytest

from scripts.prepare_connected_web import prepare


def test_connected_fonts_are_local_licensed_and_idempotent(tmp_path):
    web, fonts = tmp_path / "web", tmp_path / "fonts"
    (web / "assets").mkdir(parents=True)
    fonts.mkdir()
    manifest = web / "assets/FontManifest.json"
    manifest.write_text('[{"family":"MaterialIcons","fonts":[]}]', encoding="utf-8")
    (web / "flutter_bootstrap.js").write_text('_flutter.loader.load();', encoding="utf-8")
    for name in ("Roboto-Regular.ttf", "Roboto-Medium.ttf", "Roboto-Bold.ttf", "Roboto_LICENSE.txt"):
        (fonts / name).write_bytes(b"fixture-font-or-license")
    prepare(web, fonts)
    prepare(web, fonts)
    data = json.loads(manifest.read_text())
    assert [entry["family"] for entry in data] == ["MaterialIcons", "Roboto"]
    assert (web / "assets/fonts/Roboto_LICENSE.txt").read_bytes() == b"fixture-font-or-license"
    assert (web / "fonts/NotoSansSymbols-OFL.txt").is_file()
    assert len(list((web / "fonts/notosanssymbols").rglob("*.woff2"))) == 1
    loader = (web / "flutter_bootstrap.js").read_text()
    assert "new URL('fonts/', document.baseURI)" in loader
    assert "https://" not in loader
    assert (Path(__file__).resolve().parents[1] / "deploy/portal/fonts/README.md").is_file()


def test_unknown_bootstrap_fails_without_modifying_build(tmp_path):
    (tmp_path / "flutter_bootstrap.js").write_text("custom unsupported loader", encoding="utf-8")
    with pytest.raises(ValueError, match="no build modified"):
        prepare(tmp_path, tmp_path / "missing-fonts")
    assert (tmp_path / "flutter_bootstrap.js").read_text() == "custom unsupported loader"
