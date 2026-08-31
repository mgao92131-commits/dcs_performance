# dcs-service V1 数据访问接口文档

本文只面向调用 `dcs-service` 获取数据的客户端开发者。

本文说明：

- 如何访问接口；
- 如何查询 Historian 数据；
- 如何查询 Event 数据；
- 如何解析 CSV 和响应 Header；
- 如何使用 Event cursor 连续翻页；
- 如何保存 checkpoint；
- 如何处理错误和重试。

本文不涉及服务构建、配置、启动、停止或 DCS 现场运维。

## 1. 访问地址

`dcs-service` 本身只监听 DCS 电脑的 localhost。调用方通常通过已经配置好的安全隧道访问。

假设隧道在调用方电脑提供以下入口：

```text
http://192.168.1.10:8088
```

本文用它作为示例 Base URL：

```text
BASE_URL=http://192.168.1.10:8088
```

实际端口由服务提供方告知。

V1 没有 API Key，不需要发送：

```text
Authorization
X-DCS-API-Key
Bearer Token
```

隧道的认证和加密由外部隧道系统负责。

## 2. 协议总览

V1 提供以下 GET 接口：

| 接口 | 返回格式 | 用途 |
|---|---|---|
| `GET /health` | JSON | 检查 HTTP 进程是否可访问 |
| `GET /api/v1/info` | JSON | 获取服务版本、源时区和并发信息 |
| `GET /api/v1/tag` | JSON | 检查单个 Historian TAG |
| `GET /api/v1/history` | CSV | 获取单个 TAG 的 Historian 原始样本 |
| `GET /api/v1/events` | CSV | 获取 Event Range 或 Cursor page |

通用规则：

- 数据查询只使用 HTTP GET；
- History 和 Event 成功时返回 UTF-8 CSV；
- `/health`、`/info`、`/tag` 成功时返回 JSON；
- 所有失败响应都返回 JSON；
- 查询参数必须 URL encode；
- 参数名不能重复；
- CSV 是 UTF-8，无 BOM；
- 数字使用 invariant culture，小数点始终是 `.`。

## 3. 时间规则

所有请求和响应时间都使用服务返回的源时区，默认通常是：

```text
China Standard Time
```

调用方可以通过以下 Header 或接口确认：

```text
X-DCS-Source-TimeZone
GET /api/v1/info
```

请求时间必须是不带时区后缀的源本地时间：

```text
2026-08-30T08:00:00
2026-08-30T08:00:00.123
```

不要发送：

```text
2026-08-30T08:00:00Z
2026-08-30T08:00:00+08:00
```

服务会拒绝带 `Z` 或 offset 的时间。

调用方也不要对响应时间再次自动加 8 小时。应将它视为 `X-DCS-Source-TimeZone` 对应的本地时间。

## 4. URL 编码

TAG、时间和 source generation 都应进行 URL 编码。

例如：

```text
原始 TAG:
012-P01HZX/PID1/PV.CV

URL encoded:
012-P01HZX%2FPID1%2FPV.CV
```

source generation 中通常包含 `|`，也必须编码：

```text
原始值:
APP|2026-08-30T00:00:00.000

URL encoded:
APP%7C2026-08-30T00%3A00%3A00.000
```

推荐使用语言标准库的 URL encoder，不要手工替换字符。

## 5. 检查服务可访问性

### 请求

```http
GET /health HTTP/1.1
Host: 192.168.1.10:8088
```

### 成功响应

```json
{"status":"ok"}
```

`/health` 只表示 HTTP 进程可以响应，不表示 Historian 或 Event Journal 当前一定可用。

## 6. 获取服务信息

### 请求

```http
GET /api/v1/info HTTP/1.1
Host: 192.168.1.10:8088
```

### 响应示例

```json
{
  "service": "DcsDataService",
  "version": "1.1.0",
  "historianServer": "APP",
  "sourceTimeZone": "China Standard Time",
  "historyMaxConcurrent": 2,
  "eventMaxConcurrent": 4,
  "readOnly": true
}
```

调用方应至少记录：

- `version`；
- `sourceTimeZone`；
- `historyMaxConcurrent`；
- `eventMaxConcurrent`。

客户端并发不应明显超过服务端并发限制。

## 7. 检查 Historian TAG

### 请求参数

| 参数 | 必填 | 说明 |
|---|---|---|
| `tag` | 是 | 单个 Historian TAG 名称 |

### 请求示例

```text
GET /api/v1/tag?tag=TI-021007_AI1_PV.CV
```

### 响应示例

```json
{
  "tag": "TI-021007_AI1_PV.CV",
  "status": "HistoryTagOK",
  "dataType": "Float"
}
```

常见 `status`：

| status | 含义 |
|---|---|
| `HistoryTagOK` | TAG 已成功解析 |
| `HistoryTagUnknown` | TAG 不存在或无法识别 |
| `HistoryTagAmbiguous` | TAG 名称存在歧义 |
| `Error` | 其他解析错误 |

`dataType` metadata 获取失败时可能为空，但 TAG Handle 已成功解析时 History 查询仍可能正常工作。

## 8. 查询 Historian 数据

### 8.1 请求

```text
GET /api/v1/history
```

参数：

| 参数 | 必填 | 说明 |
|---|---|---|
| `tag` | 是 | 单个 TAG；一次请求只允许一个 TAG |
| `from` | 是 | 查询开始时间，源本地时间 |
| `to` | 是 | 查询结束时间，必须晚于 `from` |

示例：

```text
GET /api/v1/history?tag=TI-021007_AI1_PV.CV&from=2026-08-30T08%3A00%3A00&to=2026-08-30T09%3A00%3A00
```

### 8.2 成功响应 Header

```text
Content-Type: text/csv; charset=utf-8
X-DCS-Tag: TI-021007_AI1_PV.CV
X-DCS-Row-Count: 1723
X-DCS-Source-TimeZone: China Standard Time
X-DCS-From: 2026-08-30T08:00:00.0000000
X-DCS-To: 2026-08-30T09:00:00.0000000
```

建议调用方校验：

1. HTTP 状态码为 200；
2. `Content-Type` 是 `text/csv`；
3. `X-DCS-Tag` 与请求 TAG 一致；
4. CSV parser 解析出的数据行数等于 `X-DCS-Row-Count`；
5. `X-DCS-Source-TimeZone` 与客户端预期一致。

### 8.3 CSV Schema

固定列顺序：

```text
Timestamp,Value,DataType,DeltaVStatus,ArchiveStatus,SequenceNo,IsHistoryHole,IsCRHole,IsManuallyDeleted,IsManuallyInserted
```

字段说明：

| 列 | 类型/格式 | 说明 |
|---|---|---|
| `Timestamp` | DateTime 文本 | 源本地时间，最多 7 位小数秒 |
| `Value` | 文本/数字 | Historian 原始值 |
| `DataType` | 文本 | DeltaV 数据类型 |
| `DeltaVStatus` | 文本 | DeltaV 样本状态 |
| `ArchiveStatus` | 文本 | Historian archive 状态 |
| `SequenceNo` | 整数 | 样本序号 |
| `IsHistoryHole` | `true/false` | 是否为 History hole |
| `IsCRHole` | `true/false` | 是否为 continuous recorder hole |
| `IsManuallyDeleted` | `true/false` | 是否被人工删除 |
| `IsManuallyInserted` | `true/false` | 是否为人工插入 |

示例：

```csv
Timestamp,Value,DataType,DeltaVStatus,ArchiveStatus,SequenceNo,IsHistoryHole,IsCRHole,IsManuallyDeleted,IsManuallyInserted
2026-08-30T08:00:00.0000000,12.5,Float,Good,HistoryDataIsValid,1,false,false,false,false
```

### 8.4 多 TAG 查询方式

V1 不接受 `tags=[...]`。需要多个 TAG 时，调用方应逐 TAG 请求：

```text
GET TAG1
GET TAG2
GET TAG3
```

默认部署通常允许最多 2 个 History 请求同时执行。调用方建议：

- 最大并发设为 `/api/v1/info` 返回的 `historyMaxConcurrent`；
- 不要一次发送大量并发请求；
- 收到 429/503 后降低并发并退避重试；
- 单个 TAG 查询过大时拆分时间窗口。

### 8.5 History 结果过大

以下情况会返回 HTTP 413 `history_query_too_large`：

- 请求时间跨度超过服务端限制；
- AutoSplit 后累计样本数超过单请求总量限制。

正确处理方式是缩短时间范围，例如按小时或按分钟拆分。不要通过反复立即重试同一请求解决。

## 9. Event CSV Schema

Event 的固定列顺序：

```text
DateTime,FracSec,Ord,EventType,EventSubType,Category,Area,Node,Unit,Module,ModuleDescription,Attribute,State,EventLevel,Desc1,Desc2,IsArchived
```

字段说明：

| 列 | 说明 |
|---|---|
| `DateTime` | Event 源本地时间，3 位小数秒 |
| `FracSec` | cursor 的小数秒辅助字段 |
| `Ord` | 同时间事件的顺序字段 |
| `EventType` | Event 类型 |
| `EventSubType` | Event 子类型 |
| `Category` | 分类 |
| `Area` | Area |
| `Node` | Node |
| `Unit` | Unit |
| `Module` | Module |
| `ModuleDescription` | Module 描述 |
| `Attribute` | 属性 |
| `State` | 状态 |
| `EventLevel` | Event level |
| `Desc1` | 描述字段 1 |
| `Desc2` | 描述字段 2 |
| `IsArchived` | archive 标志；null 时为空字段 |

列顺序是 V1 协议的一部分。调用方最好按列名读取，同时对完整 Header Schema 做版本校验。

## 10. Event Range 查询

Range 用于按时间范围取得第一页数据，并建立后续 cursor。

### 10.1 参数

| 参数 | 必填 | 说明 |
|---|---|---|
| `from` | 是 | 开始时间，包含该边界 |
| `to` | 是 | 结束时间，不包含该边界 |
| `limit` | 否 | 本页最大记录数 |

时间范围是：

```text
[from, to)
```

请求示例：

```text
GET /api/v1/events?from=2026-08-30T08%3A00%3A00&to=2026-08-30T09%3A00%3A00&limit=1000
```

`limit` 只是每页上限，不表示整个范围最多只有这些记录。

## 11. Event Cursor 查询

Cursor 用于从上一页最后一条记录之后继续读取。

### 11.1 Cursor 组成

cursor 是以下三字段的有序元组：

```text
(DateTime, FracSec, Ord)
```

三个字段都必须保存。只保存 `DateTime` 会造成重复或漏数据。

Cursor 查询采用严格大于语义：

```text
row.cursor > supplied.cursor
```

因此不会重复返回作为 `after*` 提交的那一条记录。

### 11.2 参数

| 参数 | 必填 | 说明 |
|---|---|---|
| `afterTime` | 是 | 上一 cursor 的 `DateTime` |
| `afterFracSec` | 是 | 上一 cursor 的 `FracSec` |
| `afterOrd` | 是 | 上一 cursor 的 `Ord` |
| `sourceGeneration` | 是 | 上一响应的 source generation |
| `limit` | 否 | 本页最大记录数 |

请求示例：

```text
GET /api/v1/events?afterTime=2026-08-30T08%3A55%3A00.123&afterFracSec=123&afterOrd=456&sourceGeneration=APP%7C2026-08-30T00%3A00%3A00.000&limit=1000
```

Range 参数和 Cursor 参数不能混用。以下请求会返回 400：

```text
?from=...&to=...&afterTime=...&afterFracSec=...&afterOrd=...
```

## 12. Event 响应 Header

Range 和 Cursor 成功响应都返回：

```text
Content-Type: text/csv; charset=utf-8
X-DCS-Row-Count: 1000
X-DCS-Source-TimeZone: China Standard Time
X-DCS-Source-Generation: APP|2026-08-30T00:00:00.000
X-DCS-Has-More: true
```

有可用 next cursor 时还返回：

```text
X-DCS-Next-DateTime: 2026-08-30T09:01:23.123
X-DCS-Next-FracSec: 123
X-DCS-Next-Ord: 456
```

规则：

- `X-DCS-Row-Count` 是本页实际 CSV 数据行数；
- `X-DCS-Has-More=true` 表示查询时确认至少还有一条记录；
- next cursor 指向本页最后一条实际返回记录；
- 空 Range 没有 `X-DCS-Next-*`；
- 空 Cursor page 会重复输入 cursor，并返回 `HasMore=false`；
- 客户端不应根据 `rows == limit` 自己猜 HasMore。

## 13. Source Generation

每个 Event 响应都有：

```text
X-DCS-Source-Generation
```

generation 标识当前 Event Journal 实例。客户端必须把它与三字段 cursor 一起保存：

```text
checkpoint = {
  sourceGeneration,
  nextDateTime,
  nextFracSec,
  nextOrd
}
```

下一次 Cursor 请求必须原样提交保存的 `sourceGeneration`。

如果 Event Journal 被重建、更换或 generation 改变，服务返回：

```text
HTTP 409
error.code = source_changed
```

此时不能继续使用旧 cursor。客户端必须停止增量同步、报警并执行重新初始化流程。

## 14. Event 分页流程

推荐流程：

```text
1. 发送 Range 请求
2. 检查 HTTP 200 和 CSV Schema
3. 保存本页数据
4. 数据保存成功后，原子保存 generation + next cursor
5. 如果 HasMore=true，发送 Cursor 请求
6. 重复 2～5
7. HasMore=false 时，本轮追赶完成
```

重要顺序：

```text
先持久化数据
再推进 checkpoint
```

如果先推进 cursor，再写数据库，进程在两步之间崩溃会永久漏数据。

如果先写数据库、还没保存 cursor 就崩溃，重试时最多产生重复数据。因此远端数据库仍应使用幂等写入或唯一键去重。

### 14.1 Range `to` 边界注意事项

Range 查询受原始 `to` 限制，但切换成 Cursor 模式以后，请求中不再携带 `to`。Cursor 会继续读取 Journal 中该 cursor 之后的事件，包括原始 `to` 之后的新事件。

两种常见用途：

- 持续增量同步：这是预期行为，Range 只用于建立起点；
- 只导出固定时间窗口：客户端必须保存原始 `to`，遇到 `DateTime >= to` 的记录时停止，并丢弃边界外记录。

## 15. Event 完整性错误

以下错误不能当作普通网络错误无限重试。

### `source_changed`

```text
HTTP 409
```

Event Journal generation 已变化。停止同步并重新初始化。

### `event_cursor_expired`

```text
HTTP 409
```

客户端 cursor 早于 Journal 最早保留记录，说明 retention 已经删除了中间数据。必须报告数据缺口，不能自动从 earliest 继续。

### `retention_gap`

```text
HTTP 409
```

Range 的 `from` 早于最早保留 Event。该范围已不完整。

### `cursor_ahead`

```text
HTTP 409
```

客户端 cursor 晚于当前 Journal latest。通常表示 checkpoint 属于另一个 generation，或源发生了重建。

### `event_overflow`

```text
HTTP 503
```

EJOverflow 中存在记录。服务拒绝返回可能不完整的数据。需要人工调查。

### `event_journal_full`

```text
HTTP 503
```

JournalProperties 报告 IsFull。服务 fail-closed，需要人工调查。

## 16. CSV 解析要求

CSV 字段可能包含：

- 逗号；
- 双引号；
- CR；
- LF；
- 中文。

例如：

```text
ABC,DEF
```

会编码为：

```csv
"ABC,DEF"
```

双引号会编码为：

```csv
"He said ""TEST"""
```

不要：

- 使用 `line.Split(',')`；
- 把每个物理换行直接当作一条数据；
- 使用 Windows 当前区域设置解析小数。

必须使用标准 CSV parser，并将数字按 invariant culture 解析。

解析完成后应校验：

```text
parsed_data_rows == X-DCS-Row-Count
```

## 17. JSON 错误格式

任何数据接口失败时都不会返回半个 JSON/CSV 混合对象，而是返回 JSON 错误：

```json
{
  "ok": false,
  "error": {
    "code": "invalid_request",
    "message": "to must be after from."
  }
}
```

处理顺序：

1. 读取 HTTP 状态码；
2. 非 200 时按 JSON 解析；
3. 使用 `error.code` 分类；
4. `message` 只用于日志和人工诊断。

不要依赖英文 `message` 做程序分支。

## 18. 状态码和重试策略

| HTTP | error.code | 处理建议 |
|---:|---|---|
| `400` | `invalid_request` | 修正参数，不重试原请求 |
| `400` | `request_timeout` | 检查隧道/客户端，有限重试 |
| `404` | `not_found` | 修正 URL |
| `405` | `method_not_allowed` | 改用 GET |
| `409` | `source_changed` | 停止 Event 增量并重新初始化 |
| `409` | `event_cursor_expired` | 报告 retention 数据缺口 |
| `409` | `retention_gap` | 调整范围并明确记录缺口 |
| `409` | `cursor_ahead` | 检查 checkpoint/generation |
| `409` | `cursor_window_empty` | 重新初始化，不盲目推进 cursor |
| `413` | `history_query_too_large` | 缩小 History 时间范围 |
| `413` | `request_too_large` | 修正异常大的 HTTP 请求 |
| `429` | `service_busy` | 指数退避后重试，并降低并发 |
| `503` | `service_busy` | provider slot 等待超时，退避重试 |
| `503` | `historian_unavailable` | 暂停 History，通知服务维护方 |
| `503` | `event_unavailable` | 暂停 Event，通知服务维护方 |
| `503` | `event_overflow` | 停止同步，人工调查完整性 |
| `503` | `event_journal_full` | 停止同步，人工调查完整性 |
| `500` | `internal_error` | 保存上下文并通知服务维护方 |

推荐退避：

```text
第 1 次: 1 秒 + 随机抖动
第 2 次: 2 秒 + 随机抖动
第 3 次: 4 秒 + 随机抖动
第 4 次: 8 秒 + 随机抖动
```

应设置最大重试次数和总超时。不要让多个 worker 在收到 429 后同时立即重试。

## 19. 调用方并发建议

先读取 `/api/v1/info`：

```json
{
  "historyMaxConcurrent": 2,
  "eventMaxConcurrent": 4
}
```

建议：

- History worker 数不超过 `historyMaxConcurrent`；
- 单条 Event cursor 链只能顺序执行，不能并发推进同一 checkpoint；
- 不同独立 Event 任务总并发不超过 `eventMaxConcurrent`；
- 客户端还应设置自己的待处理队列上限；
- 429/503 增多时主动降低并发。

服务端默认整体请求队列有限。客户端不能假设所有请求都会一直等待直到执行。

## 20. curl 示例

### History

```bash
curl --fail-with-body \
  -D history.headers.txt \
  -o history.csv \
  "http://192.168.1.10:8088/api/v1/history?tag=TI-021007_AI1_PV.CV&from=2026-08-30T08%3A00%3A00&to=2026-08-30T09%3A00%3A00"
```

### Event Range

```bash
curl --fail-with-body \
  -D event.headers.txt \
  -o event.csv \
  "http://192.168.1.10:8088/api/v1/events?from=2026-08-30T08%3A00%3A00&to=2026-08-30T09%3A00%3A00&limit=1000"
```

### Event Cursor

```bash
curl --fail-with-body \
  -D event-next.headers.txt \
  -o event-next.csv \
  "http://192.168.1.10:8088/api/v1/events?afterTime=2026-08-30T08%3A55%3A00.123&afterFracSec=123&afterOrd=456&sourceGeneration=APP%7C2026-08-30T00%3A00%3A00.000&limit=1000"
```

## 21. Python：History 示例

```python
import csv
import io
import urllib.parse
import urllib.request

base_url = "http://192.168.1.10:8088"
params = urllib.parse.urlencode({
    "tag": "TI-021007_AI1_PV.CV",
    "from": "2026-08-30T08:00:00",
    "to": "2026-08-30T09:00:00",
})

url = base_url + "/api/v1/history?" + params

with urllib.request.urlopen(url, timeout=70) as response:
    expected_rows = int(response.headers["X-DCS-Row-Count"])
    source_timezone = response.headers["X-DCS-Source-TimeZone"]
    reader = csv.DictReader(
        io.TextIOWrapper(response, encoding="utf-8", newline="")
    )
    rows = list(reader)

if len(rows) != expected_rows:
    raise RuntimeError("History CSV row count mismatch")

print("timezone:", source_timezone)
print("rows:", len(rows))
```

生产代码还应捕获 `urllib.error.HTTPError` 并解析 JSON error body。

## 22. Python：Event 分页示例

以下示例展示持续增量模式。初始 Range 的 `to` 只限制第一页；切换到 Cursor 后将继续读取更新事件。

```python
import csv
import io
import json
import urllib.error
import urllib.parse
import urllib.request

BASE_URL = "http://192.168.1.10:8088"

def get_event_page(params):
    url = BASE_URL + "/api/v1/events?" + urllib.parse.urlencode(params)
    try:
        with urllib.request.urlopen(url, timeout=70) as response:
            headers = dict(response.headers.items())
            rows = list(csv.DictReader(
                io.TextIOWrapper(response, encoding="utf-8", newline="")
            ))
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8")
        error = json.loads(body)
        raise RuntimeError(
            "dcs-service HTTP %s: %s" %
            (exc.code, error.get("error", {}).get("code"))
        )

    expected = int(headers["X-DCS-Row-Count"])
    if len(rows) != expected:
        raise RuntimeError("Event CSV row count mismatch")
    return headers, rows

def persist_rows(rows):
    # 在这里使用数据库事务和幂等写入。
    pass

def save_checkpoint(generation, cursor):
    # 数据提交成功后，再在同一事务或可靠顺序中保存 checkpoint。
    pass

headers, rows = get_event_page({
    "from": "2026-08-30T08:00:00",
    "to": "2026-08-30T09:00:00",
    "limit": 1000,
})

generation = headers["X-DCS-Source-Generation"]

while True:
    persist_rows(rows)

    next_time = headers.get("X-DCS-Next-DateTime")
    next_frac = headers.get("X-DCS-Next-FracSec")
    next_ord = headers.get("X-DCS-Next-Ord")

    if next_time is not None:
        cursor = (next_time, next_frac, next_ord)
        save_checkpoint(generation, cursor)

    if headers["X-DCS-Has-More"].lower() != "true":
        break

    headers, rows = get_event_page({
        "afterTime": cursor[0],
        "afterFracSec": cursor[1],
        "afterOrd": cursor[2],
        "sourceGeneration": generation,
        "limit": 1000,
    })

    if headers["X-DCS-Source-Generation"] != generation:
        raise RuntimeError("Event source generation changed")
```

注意：当客户端已经追到查询时刻的 Journal latest，`HasMore=false`。持续同步程序应在下一轮轮询时，用已保存 cursor 再发起 Cursor 请求，而不是重新发送初始 Range。

## 23. PowerShell：保存 CSV 和读取 Header

```powershell
$tag = [Uri]::EscapeDataString("TI-021007_AI1_PV.CV")
$from = [Uri]::EscapeDataString("2026-08-30T08:00:00")
$to = [Uri]::EscapeDataString("2026-08-30T09:00:00")
$url = "http://192.168.1.10:8088/api/v1/history?tag=$tag&from=$from&to=$to"

$request = [Net.HttpWebRequest]::Create($url)
$request.Method = "GET"
$request.Timeout = 70000
$response = $request.GetResponse()

try {
    Write-Host "Rows:" $response.Headers["X-DCS-Row-Count"]
    Write-Host "TimeZone:" $response.Headers["X-DCS-Source-TimeZone"]

    $input = $response.GetResponseStream()
    $output = [IO.File]::Create("history.csv")
    try {
        $buffer = New-Object byte[] 8192
        while (($read = $input.Read($buffer, 0, $buffer.Length)) -gt 0) {
            $output.Write($buffer, 0, $read)
        }
    } finally {
        $output.Close()
        $input.Close()
    }
} finally {
    $response.Close()
}
```

## 24. 调用方验收清单

- [ ] 能通过隧道访问 `/health`；
- [ ] `/api/v1/info` 的时区符合预期；
- [ ] `/api/v1/tag` 能识别真实 TAG；
- [ ] History CSV Header Schema 完全一致；
- [ ] Event CSV Header Schema 完全一致；
- [ ] 使用标准 CSV parser，可正确读取逗号、引号和换行字段；
- [ ] 解析行数与 `X-DCS-Row-Count` 一致；
- [ ] 不向时间追加 `Z` 或 offset；
- [ ] Event checkpoint 保存 generation 和全部三个 cursor 字段；
- [ ] checkpoint 只在数据持久化成功后推进；
- [ ] Range 和 Cursor 参数不会混用；
- [ ] 客户端使用 `X-DCS-Has-More`，不自行猜分页状态；
- [ ] 客户端识别并停止处理完整性类 409/503；
- [ ] 429/临时 503 使用有限指数退避；
- [ ] History 并发不超过 `/info` 返回值；
- [ ] 同一条 Event cursor 链严格顺序执行；
- [ ] 固定范围导出会在原始 `to` 边界停止；
- [ ] 持续同步在 `HasMore=false` 后会保存 cursor 并进入下一轮轮询。
