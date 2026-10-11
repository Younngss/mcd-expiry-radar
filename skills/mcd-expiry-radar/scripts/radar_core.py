"""Normalize only verified MCP fields into an expiry-oriented action list."""
from __future__ import annotations

import calendar
import hashlib
import json
from datetime import datetime, time, timedelta, timezone
from decimal import Decimal, InvalidOperation

CST = timezone(timedelta(hours=8))
TOOL_NAMES = ["now-time-info", "query-my-coupons", "query-my-account",
              "campaign-calendar", "available-coupons", "mall-points-products"]


def parse_time(value, end=False):
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        value = value.strip()
        if len(value) == 10:
            day = datetime.strptime(value, "%Y-%m-%d").date()
            return datetime.combine(day, time.max if end else time.min, CST)
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return dt.replace(tzinfo=CST) if dt.tzinfo is None else dt.astimezone(CST)
    except ValueError:
        return None


def number(value):
    try:
        n = Decimal(str(value))
        return float(n) if n.is_finite() and n >= 0 else None
    except (InvalidOperation, ValueError):
        return None


def fingerprint(*parts):
    return hashlib.sha256(json.dumps(parts, ensure_ascii=False, sort_keys=True).encode()).hexdigest()[:20]


def classify_coupon(coupon, now):
    start = parse_time(coupon.get("tradeStartDate"))
    end = parse_time(coupon.get("tradeEndDate"), end=True)
    if start and end and start > end:
        return "unverified", 4, "有效期待核验"
    if end and end < now:
        return "expired", 9, "已过期"
    if start and start > now:
        return "upcoming", 3, "尚未生效"
    if coupon.get("enable") != 1:
        return "unverified", 4, "当前不可用 / 待核验"
    if not end or not start or start > end:
        return "unverified", 4, "有效期待核验"
    days = (end.date() - now.date()).days
    if days == 0:
        return "today", 0, "今天到期"
    if days <= 3:
        return "urgent", 1, f"{days} 天后到期"
    if days <= 7:
        return "week", 2, f"{days} 天后到期"
    return "active", 3, f"{days} 天后到期"


def data_for(raw, tool, expected):
    result = raw.get(tool)
    if not isinstance(result, dict):
        return None
    if "code" in result and str(result["code"]) != "200":
        return None
    data = result.get("data")
    return data if isinstance(data, expected) else None


def build_report(raw: dict, *, now: datetime | None = None, mode="live", errors=None,
                 coupon_complete=True, horizon=7):
    errors = dict(errors or {})
    clock = data_for(raw, "now-time-info", dict)
    official_now = parse_time(clock.get("datetime")) if clock else None
    now = (now or official_now or datetime.now(CST)).astimezone(CST)
    sources = []
    for tool in TOOL_NAMES:
        expected = list if tool in {"available-coupons", "mall-points-products"} else dict
        value = data_for(raw, tool, expected)
        valid = value is not None and tool not in errors
        if tool == "query-my-coupons" and valid:
            valid = isinstance(value.get("coupons"), list)
        if tool == "campaign-calendar" and valid:
            valid = isinstance(value.get("dailyList"), list)
        if tool == "query-my-account" and valid:
            valid = number(value.get("availablePoint")) is not None
        if tool == "now-time-info" and valid:
            valid = official_now is not None
        sources.append({"tool": tool, "status": "ok" if valid else "error",
                        "message": errors.get(tool, "已读取" if valid else "数据不可用或结构变化")})
    ok = {s["tool"] for s in sources if s["status"] == "ok"}
    items = []
    anomalies = []
    coupon_data = data_for(raw, "query-my-coupons", dict) or {}
    seen = set()
    for index, c in enumerate(coupon_data.get("coupons", []) if "query-my-coupons" in ok else []):
        if not isinstance(c, dict):
            anomalies.append("卡包中有无法识别的条目，已跳过。")
            coupon_complete = False
            continue
        identity = c.get("id")
        if identity and identity in seen:
            continue
        if identity:
            seen.add(identity)
        state, priority, badge = classify_coupon(c, now)
        end = parse_time(c.get("tradeEndDate"), end=True)
        title = str(c.get("title") or "未命名优惠券")
        discount = c.get("discountInfo") if isinstance(c.get("discountInfo"), dict) else {}
        price_text = str(discount.get("discountDesc") or "")
        value = discount.get("discountValue")
        if value not in (None, ""):
            price_text += f" {discount.get('discountTypeText', '')}{value}"
        tags = [str(t.get("label")) for t in (c.get("tags") or []) if isinstance(t, dict) and t.get("label")]
        items.append({
            "id": fingerprint("coupon", identity or [index, title, c.get("tradeEndDate")]),
            "kind": "coupon", "title": title, "subtitle": str(c.get("subtitle") or "已有卡包权益"),
            "state": state, "priority": priority, "badge": badge,
            "date": end.date().isoformat() if end else None,
            "start": str(c.get("tradeStartDate") or ""), "end": str(c.get("tradeEndDate") or ""),
            "terms": str(c.get("datetimeText") or "时段限制请在官方渠道核验"),
            "price": price_text.strip(), "tags": tags, "source": "query-my-coupons",
            "action": "若本来就计划用餐，先核验门店、渠道与可用时段，再考虑使用。",
            "caution": "持有不代表当前门店或订单可用；官方用券价格不等于节省金额。",
            "calendar": state in {"today", "urgent", "week", "active", "upcoming"} and end is not None,
        })
    if not coupon_complete:
        anomalies.append("卡包未完整读取（官方最多 5 页），以下数量仅代表已读取部分。")

    account = data_for(raw, "query-my-account", dict) or {}
    points = {
        "available": number(account.get("availablePoint")) if "query-my-account" in ok else None,
        "this_month": number(account.get("currentMouthExpirePoint")) if "query-my-account" in ok else None,
        "next_month": number(account.get("nextMouthExpirePoint")) if "query-my-account" in ok else None,
    }
    if points["this_month"] and points["this_month"] > 0:
        last = calendar.monthrange(now.year, now.month)[1]
        review = now.replace(day=max(1, last - 3), hour=9, minute=0, second=0, microsecond=0)
        review_date = max(now.date(), review.date()).isoformat()
        items.append({"id": fingerprint("points-review", now.strftime("%Y-%m")), "kind": "points",
                      "title": f"核对本月到期的 {points['this_month']:g} 积分", "subtitle": "先看是否有合适的兑换需求",
                      "state": "urgent" if last-now.day <= 7 else "active", "priority": 1 if last-now.day <= 7 else 2,
                      "badge": "本月到期汇总", "date": review_date, "start": "", "end": "本月（精确失效时间未提供）",
                      "terms": "接口仅提供月度汇总；日历日期是主动核对日，不是精确失效日。",
                      "price": "", "tags": ["积分"], "source": "query-my-account",
                      "action": "查看积分商城，核验兑换积分、现金和后续核销成本后再决定。",
                      "caution": "不把积分换算成人民币，不自动兑换，不为消耗积分额外消费。", "calendar": True})

    campaign = data_for(raw, "campaign-calendar", dict) or {}
    for day in campaign.get("dailyList", []) if "campaign-calendar" in ok else []:
        date = parse_time(day.get("date")) if isinstance(day, dict) else None
        if not date or not now.date() <= date.date() <= now.date() + timedelta(days=horizon):
            continue
        for event in (day.get("events") or []):
            if not isinstance(event, dict):
                continue
            title = str(event.get("activityTitle") or "活动详情待核验")
            identity = fingerprint("campaign", date.date().isoformat(), event.get("activityCode") or title)
            if any(i["id"] == identity for i in items):
                continue
            items.append({"id": identity, "kind": "campaign", "title": title,
                          "subtitle": str(event.get("activitySubTitle") or "官方活动日历"),
                          "state": "active" if date.date() == now.date() else "upcoming", "priority": 5,
                          "badge": "今天活动" if date.date() == now.date() else "近期活动", "date": date.date().isoformat(),
                          "start": date.date().isoformat(), "end": "", "terms": "日期仅代表活动日历展示日，参与条件需核验。",
                          "price": ("官方标价 " + str(event["price"])) if event.get("price") not in (None, "") else "",
                          "tags": ["活动"], "source": "campaign-calendar",
                          "action": "有兴趣再查看官方活动详情，确认地区、时间和参与条件。",
                          "caution": "活动信息不等于已报名、已预约或已领券。", "calendar": True})

    claimable = []
    for c in (data_for(raw, "available-coupons", list) or []) if "available-coupons" in ok else []:
        if isinstance(c, dict) and c.get("couponStatus") == "CAN_GET":
            claimable.append({"title": str(c.get("couponName") or "未命名券"), "label": str(c.get("label") or "可领取")})

    products = []
    stale_products = 0
    unknown_products = 0
    for p in (data_for(raw, "mall-points-products", list) or []) if "mall-points-products" in ok else []:
        if not isinstance(p, dict) or p.get("status") != 2:
            continue
        start = parse_time(p.get("upTime"))
        end = parse_time(p.get("downTime"), end=True)
        if end and end < now:
            stale_products += 1
            continue
        if start and start > now:
            continue
        if not start or not end or start > end:
            unknown_products += 1
            continue
        products.append({"title": str(p.get("spuName") or "未命名商品"), "points": number(p.get("point")),
                         "cash": str(p.get("price") or "待核验"), "category": str(p.get("catName") or ""),
                         "end": str(p.get("downTime")), "note": "列表价格仅供参考；SKU、库存及券核销额外金额需查官方详情。"})
    if stale_products:
        anomalies.append(f"商城有 {stale_products} 条标记上架但已超过兑换截止时间，已过滤。")
    if unknown_products:
        anomalies.append(f"商城有 {unknown_products} 条缺少有效时间信息，未列入当前兑换预览。")
    items.sort(key=lambda x: (x["priority"], x.get("date") or "9999", x["title"]))
    coupon_items = [i for i in items if i["kind"] == "coupon"]
    return {
        "mode": mode, "observed_at": now.isoformat(), "timezone": "UTC+08:00",
        "clock_source": "now-time-info" if official_now else "本机时间（官方时间不可用）",
        "horizon": horizon, "sources": sources, "coupon_complete": coupon_complete,
        "items": items, "points": points, "claimable": claimable, "products": products,
        "anomalies": list(dict.fromkeys(anomalies)),
        "counts": {"coupons": len(coupon_items) if "query-my-coupons" in ok else None,
                   "urgent": sum(i["state"] in {"today", "urgent", "week"} for i in coupon_items) if "query-my-coupons" in ok else None,
                   "campaigns": sum(i["kind"] == "campaign" for i in items) if "campaign-calendar" in ok else None},
    }


def ics_escape(value):
    return str(value).replace("\\", "\\\\").replace("\r", "").replace("\n", "\\n").replace(";", "\\;").replace(",", "\\,")


def fold_line(line):
    chunks, current = [], ""
    for char in line:
        if len((current + char).encode("utf-8")) > 73:
            chunks.append(current)
            current = " "
        current += char
    return "\r\n".join(chunks + [current])


def make_ics(report, selected_ids=None):
    """Stable all-day review reminders; coupon end dates stay date-only."""
    lines = ["BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:-//MCD Expiry Radar//ZH", "CALSCALE:GREGORIAN"]
    stamp = parse_time(report["observed_at"]).astimezone(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    for item in report["items"]:
        if not item.get("calendar") or (selected_ids is not None and item["id"] not in selected_ids):
            continue
        day = datetime.strptime(item["date"], "%Y-%m-%d").date()
        prefix = {"coupon": "核对到期券：", "campaign": "关注活动：", "points": "积分核对："}[item["kind"]]
        description = "\n".join([item["action"], item["terms"], item["caution"], "来源：" + item["source"]])
        lines.extend(["BEGIN:VEVENT", f"UID:{item['id']}@mcd-expiry-radar.local", f"DTSTAMP:{stamp}",
                      f"DTSTART;VALUE=DATE:{day:%Y%m%d}", f"DTEND;VALUE=DATE:{day + timedelta(days=1):%Y%m%d}",
                      "SUMMARY:" + ics_escape(prefix + item["title"]), "DESCRIPTION:" + ics_escape(description),
                      "BEGIN:VALARM", "ACTION:DISPLAY", "TRIGGER:-P1D", "DESCRIPTION:核对权益与使用条件",
                      "END:VALARM", "END:VEVENT"])
    lines.append("END:VCALENDAR")
    return "\r\n".join(fold_line(line) for line in lines) + "\r\n"


def markdown_report(report):
    mode = "模拟数据演示" if report["mode"] == "demo" else "真实接口快照 · 仅供本人使用"
    lines = ["# 麦麦到期雷达", "", mode, "", "查询时间：" + report["observed_at"], "",
             "已有权益优先；有用餐需求再使用。卡包持有不等于当前订单可用。", "",
             "## 行动清单", ""]
    for item in report["items"]:
        lines += [f"- **{item['title']}** · {item['badge']} · {item.get('date') or '日期待核验'}",
                  f"  {item['action']} 来源：`{item['source']}`。", f"  条件：{item['terms']}"]
    if not report["items"]:
        lines += ["当前没有可展示的行动条目；请同时检查以下数据源状态。"]
    lines += ["", "## 数据状态", ""]
    lines += [f"- `{s['tool']}`：{s['message']}" for s in report["sources"]]
    lines += ["- " + a for a in report["anomalies"]]
    return "\n".join(lines) + "\n"
