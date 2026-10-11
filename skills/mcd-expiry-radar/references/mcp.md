# MCP 接入细节

官方服务：`https://mcp.mcd.cn`，Streamable HTTP。通过 `Authorization: Bearer ${MCD_MCP_TOKEN}` 传递凭据。

执行 `initialize → notifications/initialized → tools/list → tools/call`，保留服务器返回的会话标识和协商协议版本。先发现工具，再调用；不猜测不存在的字段。

| 工具 | 参数 | 用途 |
| --- | --- | --- |
| now-time-info | `{}` | 官方北京时间，优先读 `data.datetime` |
| query-my-coupons | `{"page":"1","pageSize":"200"}` | 读取 `data.coupons` 和 `data.totalPages`；最多5页 |
| query-my-account | `{}` | `availablePoint`、`currentMouthExpirePoint`、`nextMouthExpirePoint` |
| campaign-calendar | `{}` | 默认当月完整日历；在本机过滤未来7天。`specifiedDate` 只返回锚点附近三天，不能代替全周查询 |
| available-coupons | `{}` | `data` 数组；仅 `couponStatus == "CAN_GET"` 确认可领，不自动领取 |
| mall-points-products | `{"catRuleIds":"1>4"}` | 商品券列表；同时核对 `status == 2`、`upTime` 和 `downTime` |

结果一般含 `code: 200` 和 `data`，时间工具只有 `data`。有 `isError`、非200业务码、缺失必需数据或解析失败都应视为失败。HTTP 401/403 提示检查凭据，429 最多进行一次有上限的延迟重试，其他网络错误不循环请求。

卡包敏感字段：`id`、`code`；积分账户：`accountId`。公开报告不保留这些字段。内部日历 UID 使用哈希后的标识，不嵌入券码。

日期只有 `yyyy-MM-dd` 时，开始按当日00:00，结束按当日23:59:59.999999解释。时段限制保留原文，不能由日期有效推断当前时段能用。`enable == 1` 也不替代门店校验。

积分提醒采用当月最后一天前三天作为建议核对日；已过该日则建议今天核对。精确过期时间仍需在官方账户核验。

官方指南：[M-China/mcd-mcp-server](https://github.com/M-China/mcd-mcp-server)。实时 schema 可通过 `python3 scripts/radar.py inspect` 查看。
