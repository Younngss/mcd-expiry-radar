#!/usr/bin/env python3
"""Build source and self-contained skill archives from an explicit allowlist."""
import hashlib
import json
import zipfile
from pathlib import Path

from check_release import ROOT, check


def main():
    files = check()
    out = ROOT / "dist"
    out.mkdir(exist_ok=True)
    for name, prefix, included in [
        ("mcd-expiry-radar-source.zip", "mcd-expiry-radar", files),
        ("mcd-expiry-radar-skill.zip", "", [p for p in files if p.is_relative_to(ROOT / "skills")]),
    ]:
        with zipfile.ZipFile(out / name, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            for path in included:
                relative = path.relative_to(ROOT / "skills") if not prefix else Path(prefix) / path.relative_to(ROOT)
                archive.write(path, str(relative))
        print("已生成：" + str(out / name))
    manifest = [{"path": p.relative_to(ROOT).as_posix(), "sha256": hashlib.sha256(p.read_bytes()).hexdigest()} for p in files]
    (out / "public-manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n")


if __name__ == "__main__":
    main()
