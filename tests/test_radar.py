import copy
import json
import os
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "skills" / "mcd-expiry-radar" / "scripts"))
from mcd_client import MCPClient, MCPError, decode_response
from radar_core import CST, build_report, classify_coupon, make_ics, parse_time
from dashboard import render_dashboard
from radar import collect, save_report


def demo():
    return json.loads((ROOT / "skills/mcd-expiry-radar/assets/demo.json").read_text())


class DateTests(unittest.TestCase):
    def setUp(self):
        self.now = datetime(2026, 10, 10, 23, 59, 59, tzinfo=CST)
        self.coupon = {"tradeStartDate": "2026-10-01", "tradeEndDate": "2026-10-10", "enable": 1}

    def test_date_only_end_is_inclusive(self):
        self.assertEqual(classify_coupon(self.coupon, self.now)[0], "today")

    def test_exact_time_expires_inside_day(self):
        self.coupon["tradeEndDate"] = "2026-10-10 23:00:00"
        self.assertEqual(classify_coupon(self.coupon, self.now)[0], "expired")

    def test_timezone_conversion(self):
        self.assertEqual(parse_time("2026-10-10T16:30:00Z").day, 11)
        self.assertEqual(parse_time("2026-10-10T16:30:00Z").hour, 0)

    def test_future_disabled_coupon_remains_upcoming(self):
        self.coupon.update(tradeStartDate="2026-10-12", tradeEndDate="2026-10-13", enable=0)
        self.assertEqual(classify_coupon(self.coupon, self.now)[0], "upcoming")

    def test_missing_dates_not_actionable(self):
        self.assertEqual(classify_coupon({"enable": 1}, self.now)[0], "unverified")

    def test_reversed_dates_not_upcoming(self):
        self.coupon.update(tradeStartDate="2026-10-20", tradeEndDate="2026-10-15")
        self.assertEqual(classify_coupon(self.coupon, self.now)[0], "unverified")

    def test_disabled_coupon_not_urgent(self):
        self.coupon["enable"] = 0
        self.assertEqual(classify_coupon(self.coupon, self.now)[0], "unverified")

    def test_invalid_date(self):
        self.assertIsNone(parse_time("2026-02-30"))


class ReportTests(unittest.TestCase):
    def test_expected_action_priorities(self):
        report = build_report(demo(), mode="demo")
        self.assertEqual(report["counts"]["urgent"], 3)
        self.assertEqual(report["counts"]["campaigns"], 4)
        self.assertEqual(report["items"][0]["state"], "today")

    def test_expired_mall_listing_filtered_even_when_on_sale(self):
        report = build_report(demo())
        self.assertEqual(len(report["products"]), 2)
        self.assertTrue(any("1 条" in x for x in report["anomalies"]))

    def test_missing_sources_are_unknown_not_zero(self):
        report = build_report({})
        self.assertIsNone(report["counts"]["coupons"])
        self.assertIsNone(report["points"]["available"])
        self.assertTrue(all(s["status"] == "error" for s in report["sources"]))

    def test_business_failure_does_not_become_empty_success(self):
        raw = demo()
        raw["query-my-coupons"]["code"] = 500
        self.assertIsNone(build_report(raw)["counts"]["coupons"])

    def test_schema_drift_marked_as_failure(self):
        raw = demo()
        raw["query-my-coupons"]["data"] = {"unfamiliar": []}
        self.assertIsNone(build_report(raw)["counts"]["coupons"])

    def test_sensitive_fields_removed_and_ids_hashed(self):
        raw = demo()
        raw["query-my-coupons"]["data"]["coupons"][0].update(code="PRIVATE-VOUCHER-CODE", id="PRIVATE-ID")
        raw["query-my-account"]["data"]["accountId"] = "PRIVATE-ACCOUNT"
        output = json.dumps(build_report(raw))
        for secret in ["PRIVATE-VOUCHER-CODE", "PRIVATE-ID", "PRIVATE-ACCOUNT"]:
            self.assertNotIn(secret, output)

    def test_duplicate_id_deduplicated_but_identical_distinct_coupons_retained(self):
        raw = demo()
        coupons = raw["query-my-coupons"]["data"]["coupons"]
        coupons.extend([copy.deepcopy(coupons[0]), dict(coupons[0], id="another-voucher")])
        self.assertEqual(build_report(raw)["counts"]["coupons"], 7)

    def test_coupon_price_is_not_savings(self):
        item = build_report(demo())["items"][0]
        self.assertIn("用券价格", item["price"])
        self.assertNotIn("savings", item)

    def test_monthly_points_do_not_invent_exact_expiry(self):
        report = build_report(demo())
        item = next(i for i in report["items"] if i["kind"] == "points")
        self.assertEqual(item["date"], "2026-10-28")
        self.assertIn("精确失效时间未提供", item["end"])

    def test_future_campaign_window_excludes_past_and_distant(self):
        raw = demo()
        raw["campaign-calendar"]["data"]["dailyList"].extend([
            {"date": "2026-10-09", "events": [{"activityTitle": "Past"}]},
            {"date": "2026-10-18", "events": [{"activityTitle": "Distant"}]},
        ])
        self.assertEqual(build_report(raw)["counts"]["campaigns"], 4)


class CalendarAndRenderingTests(unittest.TestCase):
    def test_calendar_excludes_expired_and_uses_stable_uids(self):
        report = build_report(demo())
        ics = make_ics(report)
        self.assertNotIn("已过期权益", ics)
        self.assertEqual(ics.count("BEGIN:VEVENT"), 10)
        self.assertEqual(make_ics(report), ics)
        self.assertIn("DTSTART;VALUE=DATE:20261010\r\nDTEND;VALUE=DATE:20261011", ics)

    def test_selected_calendar_items_only(self):
        report = build_report(demo())
        selected = {report["items"][0]["id"]}
        self.assertEqual(make_ics(report, selected).count("BEGIN:VEVENT"), 1)

    def test_calendar_utf8_folding(self):
        report = build_report(demo())
        report["items"][0]["title"] = "测试,分号;换行\n" * 40
        ics = make_ics(report)
        self.assertTrue(all(len(line.encode()) <= 75 for line in ics.split("\r\n")))
        self.assertIn("\\,", ics)
        self.assertIn("\\;", ics)

    def test_html_script_breakout_is_escaped(self):
        report = build_report(demo())
        payload = '</script><script>alert("private")</script>'
        report["items"][0]["title"] = payload
        html = render_dashboard(report)
        self.assertNotIn(payload, html)
        self.assertIn("\\u003c/script", html)

    def test_live_export_rejected_outside_private_directory(self):
        with self.assertRaises(MCPError):
            save_report(build_report(demo()), ROOT / "public.html", live=True)

    def test_live_export_has_owner_only_permissions(self):
        with tempfile.TemporaryDirectory() as tmp:
            previous = Path.cwd()
            os.chdir(tmp)
            try:
                output = save_report(build_report(demo()), ".private/test.html", live=True)
                self.assertEqual(output.stat().st_mode & 0o777, 0o600)
                self.assertEqual(output.parent.stat().st_mode & 0o777, 0o700)
                self.assertFalse(output.with_suffix(".ics").exists())
            finally:
                os.chdir(previous)


class MCPTests(unittest.TestCase):
    def test_json_and_multiline_sse(self):
        payload = '{"jsonrpc":"2.0","id":3,"result":{"data":{}}}'
        self.assertEqual(decode_response(payload, 3), {"data": {}})
        sse = ': ping\n\nevent: message\ndata: {"id": 3,\ndata: "result": {"data": {}}}\n\n'
        self.assertEqual(decode_response(sse, 3), {"data": {}})

    def test_mismatched_rpc_id_rejected(self):
        with self.assertRaises(MCPError):
            decode_response('{"id":4,"result":{}}', 3)

    def test_mutating_tools_blocked_before_network(self):
        client = MCPClient("TEST-ONLY-NO-NETWORK")
        for tool in ["auto-bind-coupons", "create-order", "mall-create-order", "draw-lottery"]:
            with self.assertRaises(MCPError):
                client.call(tool)

    def test_pagination_stops_at_official_limit(self):
        raw = demo()
        raw["query-my-coupons"]["data"]["totalPages"] = 9
        class Client:
            version = "2024-11-05"
            tools = {}
            def __init__(self): self.pages = []
            def call(self, name, args):
                if name == "query-my-coupons": self.pages.append(int(args["page"]))
                return copy.deepcopy(raw[name])
        client = Client()
        report, _ = collect(client)
        self.assertEqual(client.pages, [1, 2, 3, 4, 5])
        self.assertFalse(report["coupon_complete"])

    def test_mid_pagination_failure_keeps_partial_warning(self):
        raw = demo()
        raw["query-my-coupons"]["data"]["totalPages"] = 2
        class Client:
            version = "2024-11-05"
            tools = {}
            def call(self, name, args):
                if name == "query-my-coupons" and args["page"] == "2": raise MCPError("offline")
                return copy.deepcopy(raw[name])
        report, _ = collect(Client())
        self.assertFalse(report["coupon_complete"])
        self.assertEqual(report["counts"]["coupons"], 6)


if __name__ == "__main__":
    unittest.main()
