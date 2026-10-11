# 麦当劳 MCP 接入说明

## 实际服务与调用流程

- 服务：`https://mcp.mcd.cn`，官方远程托管服务。
- 协议：Streamable HTTP；实测协商 `2024-11-05`。
- 认证：`Authorization: Bearer ${MCD_MCP_TOKEN}`。真实凭据只从环境变量或本地被忽略的 `.env` 读取。
- 客户端：[mcd_client.py](skills/mcd-expiry-radar/scripts/mcd_client.py)，只使用 Python 标准库，支持 JSON、SSE、会话标识和工具发现。

```text
initialize → notifications/initialized → tools/list
    → now-time-info              官方时间
    → query-my-coupons           卡包，最多5页，每页200
    → query-my-account           可用积分与月度到期汇总
    → campaign-calendar          当月活动，本机筛选未来7天
    → available-coupons          可领券信息
    → mall-points-products       商品券兑换预览
    → 字段核验、时间判断、敏感字段移除
    → .private/ 中的个人报告 → 用户自行选择日历提醒
```

## Tool、输入与业务价值

| 实际使用的 Tool | 实际参数 | 使用字段 | 业务价值 |
| --- | --- | --- | --- |
| now-time-info | `{}` | `data.datetime` | 避免本机时区不同导致到期日判断错误 |
| query-my-coupons | `{"page":"1","pageSize":"200"}` | `coupons`、起止日期、enable、datetimeText、discountInfo、tags、totalPages | 梳理已持有权益，按到期顺序提醒并保留券面条件 |
| query-my-account | `{}` | `availablePoint`、`currentMouthExpirePoint`、`nextMouthExpirePoint` | 区分可用余额与将过期积分，不虚构精确失效时间 |
| campaign-calendar | `{}` | `dailyList.date`、events 的标题和副标题 | 整理今天至未来7天活动，避免把过往活动推荐为近期活动 |
| available-coupons | `{}` | `couponName`、`couponStatus`、`label` | 可领与已持有分开，只显示 CAN_GET，不自动领取 |
| mall-points-products | `{"catRuleIds":"1>4"}` | `status`、upTime、downTime、point、price、catName | 交叉核验状态与时间后预览兑换选项，不自动扣积分 |

## 真实接入验证

**2026-10-10 21:25（北京时间）**，使用本人申请的 Token 对官方服务进行了真实调用。工具发现返回35个工具，本项目实际调用上述6个只读工具，全部成功。不是仅配置 MCP 或用模拟数据代替接入。

不含凭据与账户数据的调用记录见 [docs/integration-evidence.json](docs/integration-evidence.json)。记录是开发期间的验证结果，不能替代未来每次查询的有效性；可用自己的 Token 重新运行：

```sh
python3 radar.py inspect
python3 radar.py live --evidence
```

实测发现商城列表中15条商品同时满足“上架状态”和“已超过兑换截止时间”。本项目没有只信状态字段，而是过滤这些条目并在报告中提示原因。该计数仅为当次查询观察，不代表商城当前或未来状态。

实测优惠券的 `discountInfo.discountDesc` 返回“用券价格”，故 `discountValue` 不能直接理解成优惠金额；本项目保留原始价格说明，不虚构累计节省金额。

## 边界与失败处理

- 卡包不进行门店、渠道、配送方式等下单规则校验；持有券不等于当前订单可用。本项目只做权益整理，不承诺当前门店可使用。
- 起止时间缺失、时间冲突、业务失败、工具缺失都显示待核验或数据不可用。卡包分页最多5页，超限或分页失败提示“已读取部分”。
- 积分只有月度到期汇总，日历给出的日期是建议核对日，非官方精确到期日。
- 日历是用户导出的本机文件，用户手动导入后才产生提醒。页面不会自动领券、预约、下单、兑换或抽奖。
- HTTP 401/403 提示核验 Token；429 最多有限重试一次；不将凭据随重定向发送到其他站点。
- 原始响应被视为不可信数据；HTML 内嵌 JSON 转义，界面使用文本节点，报告不加载远程资源。

真实报告仅保留在 `.private/`；券码、账户 ID 不进入规范化报告。公开源码、模拟演示和参赛包都不包含真实 Token 或个人账户数据。

官方资料：[接入指南](https://github.com/M-China/mcd-mcp-server)、[活动规则](https://github.com/M-China/mcd-developer-innovation-challenge/blob/main/activityGuidelines.md)。
