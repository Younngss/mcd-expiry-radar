import json
from pathlib import Path


def render_dashboard(report):
    template = Path(__file__).resolve().parents[1] / "assets" / "dashboard.html"
    # Prevent untrusted tool text from terminating the JSON script element.
    payload = json.dumps(report, ensure_ascii=False).replace("<", "\\u003c").replace(">", "\\u003e").replace("&", "\\u0026")
    return template.read_text(encoding="utf-8").replace("__REPORT_JSON__", payload)
