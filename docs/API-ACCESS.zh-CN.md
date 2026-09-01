# dcs-service V1 数据访问接口文档

本文是 dcs-service 新版“完整范围流式 CSV”协议的客户端说明。客户端只访问
dcs-service HTTP API，不需要了解服务端内部的 Historian 分段或网络转发方式。

## 1. 访问地址与通用规则

生产客户端默认访问：

```text
http://192.168.1.10:8088
```

可使用环境变量 `DCS_SERVICE_BASE_URL` 覆盖默认地址。DCS 电脑本机的监听地址
是服务实现细节，客户端不应将它作为默认地址。

V1 只使用 HTTP GET，不需要 API Key、Bearer Token 或其他应用层认证 Header。
查询参数必须使用 URL encoder。时间是服务源时区的 naive ISO 文本，不能带 `Z`
或数值 offset：

```text
2026-08-31T00:00:00
2026-08-31T02:00:00.123
```

所有时间范围均采用半开区间 `[from, to)`。

## 2. 接口总览

| 接口 | 成功格式 | 用途 |
|---|---|---|
| `GET /health` | JSON | 检查 HTTP 进程是否可访问 |
| `GET /api/v1/info` | JSON | 获取版本、源时区、并发限制和流窗口信息 |
| `GET /api/v1/tag?tag=...` | JSON | 检查 Historian TAG |
| `GET /api/v1/history?tag&from&to` | UTF-8 CSV stream | 获取一个 TAG 的完整 History 范围 |
| `GET /api/v1/events?from&to` | UTF-8 CSV stream | 获取完整 Event 范围 |

History 和 Event 查询都是一个完整范围请求。服务端可以在内部按 stream window
读取数据，但这些窗口不会暴露为客户端分页，也不限制客户端请求的时间跨度。

## 3. `/health` 与 `/api/v1/info`

`/health` 成功响应：

```json
{"status":"ok"}
```

`/api/v1/info` 示例：

```json
{
  "service": "DcsDataService",
  "version": "1.1.0",
  "historianServer": "APP",
  "sourceTimeZone": "China Standard Time",
  "historyMaxConcurrent": 2,
  "eventMaxConcurrent": 4,
  "historyStreamWindowMinutes": 60,
  "eventStreamWindowMinutes": 60,
  "readOnly": true
}
```

`historyStreamWindowMinutes` 和 `eventStreamWindowMinutes` 只描述服务端内部的
流式读取窗口。客户端不能把它们解释为“每次最多只能查询这么长时间”。客户端
并发应分别遵守 `historyMaxConcurrent` 和 `eventMaxConcurrent`。

## 4. History 完整范围流

请求只允许以下参数：

```text
GET /api/v1/history?tag=<TAG>&from=<FROM>&to=<TO>
```

例如：

```text
GET /api/v1/history?tag=TI-013008%2FAI1%2FPV.CV&from=2026-08-31T00%3A00%3A00&to=2026-08-31T02%3A00%3A00
```

客户端对一个范围只发一个 History 请求，不传 `limit`，不按 24 小时人工切段，
也不依赖 `X-DCS-Row-Count`。服务端成功响应通常使用 chunked stream，但合法的
反向代理可以改变 HTTP framing；客户端应以标准 HTTP 库正常读到 EOF 作为完整性
判定，不应手工解析 chunk framing。

成功响应必须满足：

1. HTTP 状态码为 `200`；
2. `Content-Type` 的 media type 为 `text/csv`；
3. CSV body 完整读到正常 EOF；
4. CSV Header 完全符合固定十列 Schema。

`X-DCS-Tag` 和 `X-DCS-Source-TimeZone` 如果返回可以校验，但 Header 缺失本身不
应使新版客户端失败；源时区以 `/api/v1/info` 为权威配置。

固定列顺序：

```text
Timestamp,Value,DataType,DeltaVStatus,ArchiveStatus,SequenceNo,IsHistoryHole,IsCRHole,IsManuallyDeleted,IsManuallyInserted
```

`Value` 保留服务端原始文本，不根据 `DataType` 在数据访问层自动转换。

## 5. Event 完整范围流

Event Range 请求只使用 `from` 和 `to`：

```text
GET /api/v1/events?from=<FROM>&to=<TO>
```

一次请求返回完整 `[from, to)` 范围的 Event。客户端不传 `limit`，不读取分页头，
不发起后续 Range page。服务端返回的记录必须严格按以下键升序：

```text
(DateTime, FracSec, Ord)
```

客户端必须验证：

```python
from_time <= event.timestamp < to_time
```

发现越界、倒序或重复 cursor 时应报告协议/数据完整性错误，不能排序后继续使用，
也不能静默过滤越界数据。

固定十七列顺序：

```text
DateTime,FracSec,Ord,EventType,EventSubType,Category,Area,Node,Unit,Module,ModuleDescription,Attribute,State,EventLevel,Desc1,Desc2,IsArchived
```

## 6. Event Cursor：增量同步 checkpoint

Event Cursor 不是固定 Range 查询的下一页游标，而是 Event 增量同步的 checkpoint。
当前考核查询 `get_events(start, end)` 不使用 Cursor。

需要增量同步时，请保存完整的：

```text
sourceGeneration
afterTime
afterFracSec
afterOrd
```

请求必须同时带 `to`：

```text
GET /api/v1/events?afterTime=<TIME>&afterFracSec=<N>&afterOrd=<N>&sourceGeneration=<GENERATION>&to=<TO>
```

Cursor 请求不得带 `limit`。Cursor 的结果仍需按
`(DateTime, FracSec, Ord)` 严格校验。`sourceGeneration` 变化时不能继续使用旧
checkpoint，应停止增量同步并重新初始化。

## 7. 流结束、中断与重试

正常成功不是“HTTP 200 且已经收到一部分 CSV”，而是 HTTP body 已经正常结束。
以下异常表示流不完整或读取被中断：

- `IncompleteRead`；
- `RemoteDisconnected`；
- `ConnectionResetError`；
- `BrokenPipeError`；
- socket/read timeout；
- 其他明确的 HTTP body 读取中断。

如果 CSV 已经开始输出后连接中断，服务不会再追加 JSON 错误。客户端必须：

1. 丢弃本次已读取和已解析的所有数据；
2. 将错误归类为 `incomplete_stream`；
3. 按有限退避策略重新请求完整的原始 `from/to` 范围；
4. 只有第二次完整读到 EOF 后才把结果交给上层。

禁止从中断位置续传，也禁止将 partial CSV 交给考核规则。

在 CSV body 开始前返回的 `400`、`429`、`503` 等错误按 JSON error 处理。`429`
或可重试的 `503 service_busy` 使用有限指数退避；参数错误和 Event 完整性错误
不能盲目重试。客户端可以配置一个可选的 `total_timeout_seconds`，默认不限制
整个流的下载时长；单次网络连接/读取仍受 `timeout_seconds` 限制。

## 8. CSV 解析要求

CSV 字段可能包含逗号、双引号、换行和中文。必须使用标准 CSV parser 和固定
Header Schema，不能用 `split(',')` 或把物理换行直接当作记录边界。

History 和 Event 的原始模型分别保留十列和十七列；Event 的 `DateTime` 原始文本
也可以保留，以支持协议精度和顺序校验。

## 9. curl 示例

```bash
curl --fail-with-body \
  -D history.headers.txt \
  -o history.csv \
  "http://192.168.1.10:8088/api/v1/history?tag=TI-013008%2FAI1%2FPV.CV&from=2026-08-31T00%3A00%3A00&to=2026-08-31T02%3A00%3A00"
```

```bash
curl --fail-with-body \
  -D event.headers.txt \
  -o event.csv \
  "http://192.168.1.10:8088/api/v1/events?from=2026-08-31T00%3A00%3A00&to=2026-08-31T02%3A00%3A00"
```

Cursor checkpoint 示例：

```bash
curl --fail-with-body \
  -o event-next.csv \
  "http://192.168.1.10:8088/api/v1/events?afterTime=2026-08-31T01%3A00%3A00.123&afterFracSec=123&afterOrd=456&sourceGeneration=APP%7C2026-08-31T00%3A00%3A00.000&to=2026-08-31T02%3A00%3A00"
```

## 10. 客户端验收清单

- [ ] 默认 Base URL 为 `http://192.168.1.10:8088`；
- [ ] `/api/v1/info` 能解析两个 `streamWindowMinutes` 字段；
- [ ] History 一个范围只发送一次请求，不传 `limit`；
- [ ] History 不要求 `X-DCS-Row-Count`；
- [ ] Event 一个范围只发送一次请求，不传 `limit`；
- [ ] Event 校验 `[from, to)` 和 `(DateTime, FracSec, Ord)` 顺序；
- [ ] 流中断时丢弃 partial data，并整段重试；
- [ ] 使用标准库处理 HTTP framing，不手工解析 chunk；
- [ ] 客户端并发遵守 `/api/v1/info` 限制；
- [ ] Cursor 作为 checkpoint 使用时始终带 `to`；
- [ ] 自动化测试不访问真实 DCS；
- [ ] 真实环境通过 probe 人工验证，不把单元测试当作现场连通性证明。
