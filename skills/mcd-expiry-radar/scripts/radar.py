#!/usr/bin/env python3
"""Read-only McDonald's MCP expiry radar. No third-party dependencies."""
from __future__ import annotations

import argparse
import json
import os
import sys
from functools import partial
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from dashboard import render_dashboard
from mcd_client import MCPClient, MCPError, read_token
from radar_core import TOOL_NAMES, build_report, make_ics, markdown_report

SKILL_ROOT = Path(__file__).resolve().parents[1]


def private_write(path, content):
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    os.chmod(path, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8", newline="") as stream:
        stream.write(content)


def collect(client, progress=None):
    raw, errors, calls = {}, {}, []
    complete = True
    for tool in TOOL_NAMES:
        args = {"page": "1", "pageSize": "200"} if tool == "query-my-coupons" else (
            {"catRuleIds": "1>4"} if tool == "mall-points-products" else {})
        try:
            response = client.call(tool, args)
            raw[tool] = response
            calls.append({"tool": tool, "status": "ok", "arguments": args})
            if tool == "query-my-coupons":
                data = response.get("data")
                if not isinstance(data, dict) or not isinstance(data.get("coupons"), list):
                    raise MCPError("卡包数据结构变化，无法读取。")
                try:
                    pages = int(data.get("totalPages", 1))
                except (ValueError, TypeError):
                    pages = 1
                    complete = False
                complete = complete and pages <= 5
                for page in range(2, min(pages, 5) + 1):
                    page_args = {"page": str(page), "pageSize": "200"}
                    try:
                        extra = client.call(tool, page_args)
                        coupons = extra.get("data", {}).get("coupons")
                        if not isinstance(coupons, list):
                            raise MCPError("卡包分页结构变化。")
                        data["coupons"].extend(coupons)
                        calls.append({"tool": tool, "status": "ok", "arguments": page_args})
                    except MCPError:
                        complete = False
                        calls.append({"tool": tool, "status": "partial", "arguments": page_args})
                        break
        except MCPError as exc:
            errors[tool] = str(exc)
            calls.append({"tool": tool, "status": "error", "arguments": args})
        if progress:
            progress(tool, tool not in errors)
    report = build_report(raw, errors=errors, coupon_complete=complete)
    evidence = {
        "observed_at": report["observed_at"], "server": "https://mcp.mcd.cn",
        "transport": "Streamable HTTP", "protocol": client.version,
        "tools_discovered": len(client.tools), "calls": calls,
        "note": "仅记录工具名、非敏感参数和成功状态，不含账户数据或凭据。",
    }
    return report, evidence


def save_report(report, output, live=False):
    output = Path(output).resolve()
    if live:
        private_root = (Path.cwd() / ".private").resolve()
        if not output.is_relative_to(private_root):
            raise MCPError("真实账户报告只能输出到当前目录的 .private/ 中。")
    if live:
        private_root.mkdir(exist_ok=True, mode=0o700)
        private_root.chmod(0o700)
    output.parent.mkdir(parents=True, exist_ok=True, mode=0o700 if live else 0o755)
    files = {
        output: render_dashboard(report),
        output.with_suffix(".json"): json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        output.with_suffix(".md"): markdown_report(report),
    }
    if not live:
        files[output.with_suffix(".ics")] = make_ics(report)
    for path, content in files.items():
        if live:
            private_write(path, content)
        else:
            path.write_text(content, encoding="utf-8", newline="")
    return output


class DashboardHandler(BaseHTTPRequestHandler):
    """Serve exactly one dashboard. Never expose the workspace or .env."""
    def __init__(self, *args, dashboard_path, **kwargs):
        self.dashboard_path = dashboard_path
        super().__init__(*args, **kwargs)

    def do_GET(self):
        if self.headers.get("Host") not in {f"127.0.0.1:{self.server.server_port}", f"localhost:{self.server.server_port}"}:
            self.send_error(403)
            return
        if self.path.split("?", 1)[0] not in ("/", "/index.html"):
            self.send_error(404)
            return
        payload = self.dashboard_path.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(payload)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, *args):
        pass


def main(argv=None):
    parser = argparse.ArgumentParser(description="麦麦到期雷达：查询已有权益、整理到期清单。")
    sub = parser.add_subparsers(dest="command", required=True)
    demo = sub.add_parser("demo", help="生成不联网的模拟演示")
    demo.add_argument("--out", default="docs/demo.html")
    live = sub.add_parser("live", help="用本人的 Token 查询真实权益，仅保存到 .private/")
    live.add_argument("--out", default=".private/dashboard.html")
    live.add_argument("--evidence", action="store_true", help="保存不含账户数据的接入验证记录")
    sub.add_parser("inspect", help="查看六个只读工具的实时参数结构")
    serve = sub.add_parser("serve", help="仅向本机展示指定报告")
    serve.add_argument("--file", default="docs/demo.html")
    serve.add_argument("--port", type=int, default=8765)
    args = parser.parse_args(argv)
    try:
        if args.command == "demo":
            report = build_report(json.loads((SKILL_ROOT / "assets" / "demo.json").read_text(encoding="utf-8")), mode="demo")
            print("模拟演示已生成：" + str(save_report(report, args.out)))
        elif args.command == "live":
            # Check destination before reading private account data.
            output = Path(args.out).resolve()
            if not output.is_relative_to((Path.cwd() / ".private").resolve()):
                raise MCPError("真实账户报告只能输出到当前目录的 .private/ 中。")
            client = MCPClient(read_token(Path.cwd())).connect()
            report, evidence = collect(client, lambda tool, ok: print(tool + ("：已读取" if ok else "：未完成"), flush=True))
            output = save_report(report, args.out, live=True)
            if args.evidence:
                private_write(Path(".private/integration-evidence.json"), json.dumps(evidence, ensure_ascii=False, indent=2) + "\n")
            print("个人报告已生成：" + str(output))
            if any(s["status"] != "ok" for s in report["sources"]) or not report["coupon_complete"]:
                print("部分来源未完整可用，报告已明确标注。", file=sys.stderr)
                return 2
        elif args.command == "inspect":
            client = MCPClient(read_token(Path.cwd())).connect()
            for tool in TOOL_NAMES:
                spec = client.tools.get(tool)
                print(json.dumps({"tool": tool, "inputSchema": spec.get("inputSchema") if spec else None}, ensure_ascii=False))
        else:
            path = Path(args.file).resolve()
            if not path.is_file():
                raise MCPError("报告不存在，请先运行 demo 或 live。")
            handler = partial(DashboardHandler, dashboard_path=path)
            with ThreadingHTTPServer(("127.0.0.1", args.port), handler) as server:
                print(f"在浏览器打开 http://127.0.0.1:{server.server_port}；按 Ctrl+C 停止。", flush=True)
                server.serve_forever()
    except KeyboardInterrupt:
        return 0
    except (MCPError, OSError, ValueError) as exc:
        print("未完成：" + str(exc), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
