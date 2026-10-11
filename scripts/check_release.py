#!/usr/bin/env python3
"""Check only the explicit public file set; never print a secret match."""
import hashlib
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DECLARATION_SHA256 = "cd28101bcb29cc8fb24b372fe91ce6b42c4a1487a54e01f7ade0c8c6a84241b7"
REQUIRED = ["README.md", "CONTEST_DECLARATION.md", "MCP_INTEGRATION.md", "mcp-config.example.json", "radar.py"]
TOP_FILES = REQUIRED + ["LICENSE", ".gitignore", ".env.example"]
DOC_FILES = ["docs/SUBMISSION.md", "docs/demo.html", "docs/demo.json", "docs/demo.md", "docs/demo.ics",
             "docs/integration-evidence.json", "docs/preview.jpg"]


def public_files():
    paths = [ROOT / name for name in TOP_FILES + DOC_FILES]
    for directory, suffixes in [("skills", {".py", ".md", ".json", ".html", ".yaml"}),
                                ("tests", {".py"}), ("scripts", {".py"}), (".github/workflows", {".yml"})]:
        paths.extend(p for p in (ROOT / directory).rglob("*") if p.is_file() and p.suffix in suffixes
                     and "__pycache__" not in p.parts)
    return sorted({p for p in paths if p.is_file()})


def check():
    problems = []
    for name in REQUIRED:
        if not (ROOT / name).is_file():
            problems.append("缺少必需文件：" + name)
    declaration = ROOT / "CONTEST_DECLARATION.md"
    if declaration.exists() and hashlib.sha256(declaration.read_bytes()).hexdigest() != DECLARATION_SHA256:
        problems.append("参赛声明与 2026-10-10 下载的官方原文不一致。")
    config_path = ROOT / "mcp-config.example.json"
    if config_path.exists():
        config = json.loads(config_path.read_text())["mcpServers"]["mcd-mcp"]
        if config.get("headers", {}).get("Authorization") != "Bearer ${MCD_MCP_TOKEN}":
            problems.append("配置示例必须仅使用环境变量占位符。")
    token = ""
    env = ROOT / ".env"
    if env.exists():
        for line in env.read_text().splitlines():
            if line.startswith("MCD_MCP_TOKEN="):
                token = line.split("=", 1)[1].strip().strip("\"'")
    for path in public_files():
        relative = path.relative_to(ROOT).as_posix()
        if path.is_symlink():
            problems.append("公开文件不允许符号链接：" + relative)
            continue
        data = path.read_bytes()
        if token and token.encode() in data:
            problems.append("发现真实凭据，禁止发布：" + relative)
        if path.suffix not in {".jpg", ".png"}:
            text = data.decode("utf-8")
            if re.search(r"Bearer\s+[A-Za-z0-9_-]{24,}", text):
                problems.append("疑似真实 Bearer 凭据：" + relative)
        if ".private" in path.parts or path.name == ".env":
            problems.append("私有文件进入发布清单。")
    if problems:
        raise RuntimeError("\n".join(problems))
    return public_files()


if __name__ == "__main__":
    files = check()
    print(f"发布检查通过：{len(files)} 个公开文件，官方声明一致，未发现本地 Token。")
