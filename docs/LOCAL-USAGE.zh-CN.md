# dcs-service 本机使用说明

## 1. 访问地址

当前局域网数据访问地址为：

```text
http://192.168.1.10:8088
```

本机目录：

```text
H:\share\dcs_service\server\runtime
```

该目录运行的是 FRP Server，负责把另一台 DCS 电脑上的只读 API 转发到局域网。它不是 `DcsDataService` 的直接运行目录。

地址区别如下：

| 使用场景 | 地址 |
| --- | --- |
| 局域网客户端访问 | `http://192.168.1.10:8088` |
| DCS 电脑本机直连 | `http://127.0.0.1:18080` |
| FRP 客户端控制连接 | `192.168.1.10:7000` |

客户端只需要访问 `192.168.1.10:8088`，不需要了解 FRP 或 DCS 电脑的内部地址。

## 2. 通用约定

所有接口均使用 HTTP `GET`，服务是只读的，不需要 API Key。

时间参数使用 Historian/Event Journal 的 source-local 时间，当前配置为 `China Standard Time`：

```text
2026-08-31T00:00:00
```

不要添加 `Z` 或时区偏移量。时间范围均为半开区间：

```text
[from, to)
```

也就是说包含 `from`，不包含 `to`，并且必须满足 `to > from`。查询参数中的 `:`, `/`, `+` 等字符应进行 URL percent-encoding。

## 3. 服务状态和诊断

### 健康检查

```http
GET http://192.168.1.10:8088/health
```

正常返回：

```json
{"status":"ok"}
```

### 服务信息

```http
GET http://192.168.1.10:8088/api/v1/info
```

返回服务版本、Historian、时区、并发数和内部窗口配置。例如：

```json
{
  "service":"DcsDataService",
  "version":"1.1.0",
  "historianServer":"APP",
  "sourceTimeZone":"China Standard Time",
  "historyMaxConcurrent":2,
  "eventMaxConcurrent":4,
  "historyStreamWindowMinutes":60,
  "eventStreamWindowMinutes":60,
  "readOnly":true
}
```

### TAG 诊断

```http
GET /api/v1/tag?tag=<TAG>
```

实际请求示例：

```text
http://192.168.1.10:8088/api/v1/tag?tag=012-P01HZX%2FPID1%2FPV.CV
```

正常的历史 TAG 应返回 `HistoryTagOK`，同时返回数据类型。

## 4. History 数据

请求指定 TAG 和完整时间范围：

```http
GET /api/v1/history?tag=<TAG>&from=<FROM>&to=<TO>
```

示例：

```text
http://192.168.1.10:8088/api/v1/history?tag=012-P01HZX%2FPID1%2FPV.CV&from=2026-08-31T00%3A00%3A00&to=2026-08-31T02%3A00%3A00
```

返回完整 CSV，表头为：

```csv
Timestamp,Value,DataType,DeltaVStatus,ArchiveStatus,SequenceNo,IsHistoryHole,IsCRHole,IsManuallyDeleted,IsManuallyInserted
```

响应主要特征：

```http
Content-Type: text/csv; charset=utf-8
Transfer-Encoding: chunked
```

History 服务端会自行按内部时间窗口读取，客户端只收到一个连续 CSV 文件，不需要分页，也不需要传 `limit`。

## 5. Event 数据

### 时间范围模式

```http
GET /api/v1/events?from=<FROM>&to=<TO>
```

示例：

```text
http://192.168.1.10:8088/api/v1/events?from=2026-08-31T00%3A00%3A00&to=2026-08-31T02%3A00%3A00
```

返回 `[from,to)` 内的全部 Event，按以下字段升序排列：

```text
(DateTime, FracSec, Ord)
```

CSV 表头为：

```csv
DateTime,FracSec,Ord,EventType,EventSubType,Category,Area,Node,Unit,Module,ModuleDescription,Attribute,State,EventLevel,Desc1,Desc2,IsArchived
```

Event 同样不支持分页，不要传：

```text
limit
HasMore
NextCursor
```

### Cursor 增量模式

Cursor 只用于同步 checkpoint，不是分页参数。请求必须包含固定的 `to`：

```http
GET /api/v1/events?afterTime=<TIME>&afterFracSec=<N>&afterOrd=<N>&sourceGeneration=<GENERATION>&to=<TO>
```

示例：

```text
http://192.168.1.10:8088/api/v1/events?afterTime=2026-08-31T00%3A00%3A03.000&afterFracSec=3513&afterOrd=51602737&sourceGeneration=APP%7C2026-08-31T00%3A00%3A00.000&to=2026-08-31T02%3A00%3A00
```

客户端应保存：

1. Event CSV 最后一行的 `DateTime`、`FracSec`、`Ord`；
2. 响应头 `X-DCS-Source-Generation`；
3. 下一轮同步时指定新的固定 `to`。

`to <= afterTime` 会在数据开始输出前被拒绝。Cursor 过期、超前或数据源 generation 变化时，请丢弃本次结果并重新建立同步范围。

## 6. PowerShell 下载示例

### 下载 History

```powershell
$base = "http://192.168.1.10:8088"
$tag = [Uri]::EscapeDataString("012-P01HZX/PID1/PV.CV")
$from = [Uri]::EscapeDataString("2026-08-31T00:00:00")
$to = [Uri]::EscapeDataString("2026-08-31T02:00:00")
$uri = "$base/api/v1/history?tag=$tag&from=$from&to=$to"

$part = ".\history.csv.part"
Invoke-WebRequest -UseBasicParsing $uri -OutFile $part
Move-Item -LiteralPath $part -Destination ".\history.csv" -Force
```

### 下载 Event

```powershell
$base = "http://192.168.1.10:8088"
$from = [Uri]::EscapeDataString("2026-08-31T00:00:00")
$to = [Uri]::EscapeDataString("2026-08-31T02:00:00")
$uri = "$base/api/v1/events?from=$from&to=$to"

$part = ".\events.csv.part"
Invoke-WebRequest -UseBasicParsing $uri -OutFile $part
Move-Item -LiteralPath $part -Destination ".\events.csv" -Force
```

先写入 `.part` 文件，下载命令成功结束后再改成正式文件名。若连接中途断开，不要使用未完成的 `.part` 文件，应重新请求完整范围。

## 7. Python 下载示例

```python
import os
import requests

BASE = "http://192.168.1.10:8088"
params = {
    "tag": "012-P01HZX/PID1/PV.CV",
    "from": "2026-08-31T00:00:00",
    "to": "2026-08-31T02:00:00",
}

temporary = "history.csv.part"
with requests.get(
    BASE + "/api/v1/history",
    params=params,
    stream=True,
    timeout=(60, 120),
) as response:
    response.raise_for_status()
    with open(temporary, "wb") as output:
        for block in response.iter_content(1024 * 1024):
            if block:
                output.write(block)

os.replace(temporary, "history.csv")
```

HTTP 客户端必须自己解析标准 chunked transfer framing，不要把 chunk 长度行写进 CSV。CSV 为 UTF-8 无 BOM，数字使用 invariant culture，字段按 CSV 规则转义。

## 8. 完成和失败判断

正常下载满足：

```text
HTTP 200
CSV 表头只有一次
响应正常结束并收到 terminating chunk
```

流式输出开始后，如果 Historian、Event Journal、网络或数据完整性检查失败，服务不会再返回 JSON 错误，而是关闭连接且不发送 terminating chunk。客户端必须将本次文件判定为不完整，并重新请求整个时间范围。

服务不返回 `X-DCS-Row-Count`，客户端如需行数应自行统计 CSV 数据行。

## 9. 常见错误

```json
{"ok":false,"error":{"code":"invalid_request","message":"..."}}
```

常见状态码：

| 状态码 | 含义 |
| --- | --- |
| 400 | 参数、时间范围或 Cursor 不合法 |
| 404 | 请求路径不存在 |
| 429 | 并发槽或请求队列已满 |
| 503 | Historian/Event Journal 不可用或完整性检查失败 |

如果返回 400/429/503，读取 JSON 错误信息；如果已经收到 CSV 后连接中断，则按“不完整下载”处理并重试整个请求。
