"""Build an SDK wheel, optionally embedding the already-built Flutter console."""
import argparse
import pathlib
import shutil
import subprocess
import tempfile
import tomllib
import zipfile


def console_archive(source, output):
    """Package the console separately so SDK consumers never install its assets."""
    from ci.artifact_manifest import inventory
    files = inventory(source)
    with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as archive:
        for name in files:
            info = zipfile.ZipInfo("web/" + name, date_time=(2026, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            archive.writestr(info, (source / name).read_bytes())


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    console = parser.add_mutually_exclusive_group()
    console.add_argument("--with-console", action="store_true", help="Legacy combined wheel for local use")
    console.add_argument("--console-archive", action="store_true", help="Build a separate console ZIP alongside the slim SDK")
    parser.add_argument("--sdist", action="store_true", help="Also build the source distribution")
    parser.add_argument("--out-dir", type=pathlib.Path,
                        help="Directory for artifacts; defaults to this checkout's dist directory")
    args = parser.parse_args()
    root = pathlib.Path(__file__).resolve().parent.parent
    # A fresh source tree prevents setuptools from reusing stale build/lib assets.
    with tempfile.TemporaryDirectory(prefix="adkl-wheel-") as directory:
        stage = pathlib.Path(directory)
        for name in ("pyproject.toml", "README.md"):
            shutil.copyfile(root / name, stage / name)
        # Apache-2.0 sections 4(a) and 4(d): recipients get the License text and
        # the NOTICE. pyproject's license-files globs resolve against this stage
        # directory, so they have to be copied in or the wheel ships the license
        # expression with nothing behind it.
        for name in tomllib.loads(
                (root / "pyproject.toml").read_text())["project"]["license-files"]:
            shutil.copyfile(root / name, stage / name)
        for source in (root / "adkl").rglob("*.py"):
            if "web" in source.relative_to(root / "adkl").parts:
                continue
            target = stage / source.relative_to(root)
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source, target)
        if args.with_console:
            source = root / "console/build/web"
            if not (source / "index.html").is_file():
                raise SystemExit("Build the Flutter console first: flutter build web --release")
            shutil.copytree(source, stage / "adkl/web")
        output = (args.out_dir or root / "dist").resolve()
        output.mkdir(parents=True, exist_ok=True)
        args_build = ["uv", "build", "--no-sources", "--out-dir", str(output)]
        if not args.sdist:
            args_build.append("--wheel")
        # With --sdist, uv builds the wheel from the sdist, exercising its contents.
        subprocess.run(args_build, cwd=stage, check=True)
    if args.console_archive:
        version = tomllib.loads((root / "pyproject.toml").read_text())["project"]["version"]
        console_archive(root / "console/build/web", output / f"adkl_console-{version}.zip")
    print(f"Built SDK artifacts in {output}")


if __name__ == "__main__":
    main()
