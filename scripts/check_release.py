"""Read-only release checks. Run before any platform build."""
from __future__ import annotations

import ast
import json
import plistlib
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main() -> int:
    tree = ast.parse((ROOT / "src/__init__.py").read_text(encoding="utf-8"))
    version = next(
        ast.literal_eval(node.value)
        for node in tree.body
        if isinstance(node, ast.Assign)
        and any(isinstance(target, ast.Name) and target.id == "__version__" for target in node.targets)
    )
    errors = []
    package = json.loads((ROOT / "frontend/package.json").read_text(encoding="utf-8"))
    plist = plistlib.loads((ROOT / "build/Info.plist").read_bytes())
    for name, value in [("frontend/package.json", package["version"]),
                        ("build/Info.plist", plist["CFBundleShortVersionString"])]:
        if value != version:
            errors.append(f"{name}: {value!r} differs from canonical {version!r}")
    paths = subprocess.check_output(["git", "ls-files", "-z"], cwd=ROOT).decode().split("\0")
    for name in filter(None, paths):
        path = ROOT / name
        if not path.is_file():
            continue
        data = path.read_bytes()
        if b"\0" in data:
            continue
        try:
            data.decode("utf-8")
        except UnicodeDecodeError:
            continue
        if path.suffix.lower() in (".bat", ".cmd"):
            if b"\n" in data.replace(b"\r\n", b""):
                errors.append(f"{name}: Windows batch file contains LF-only lines")
        elif b"\r\n" in data:
            errors.append(f"{name}: expected LF line endings")
    for error in errors:
        print("ERROR:", error)
    print(f"Insight {version}: {'FAIL' if errors else 'PASS'}")
    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
