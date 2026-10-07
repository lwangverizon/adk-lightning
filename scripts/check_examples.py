"""Validate only the maintained examples; never discover user projects for CI."""
import json
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]


def main():
    examples = json.loads((ROOT / "projects/examples.json").read_text())["projects"]
    if not examples or len(examples) != len(set(examples)):
        raise ValueError("Example list must be nonempty and unique")
    for name in examples:
        if not isinstance(name, str) or Path(name).name != name or name in {".", ".."}:
            raise ValueError("Examples must name immediate project directories")
        root = ROOT / "projects" / name
        if root.is_symlink():
            raise ValueError("Examples must not be symlinks")
        subprocess.run([sys.executable, "-m", "adkl.agent_schema", str(root / "agent.yaml")],
                       cwd=ROOT, check=True)
        subprocess.run([sys.executable, "-m", "adkl.audit", str(root)], cwd=ROOT, check=True)
        subprocess.run([sys.executable, "-m", "adkl.widget_lint", str(root / "agent.yaml"),
                        "--structural"], cwd=ROOT, check=True)
    print(f"PASS: {len(examples)} maintained examples")


if __name__ == "__main__":
    main()
