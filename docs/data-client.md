# DCS 数据访问层

本项目是考核客户端，使用 `DcsServiceClient` 通过 dcs-service V1 获取
Historian 和 Event 数据。协议细节唯一以
[`API-ACCESS.zh-CN.md`](API-ACCESS.zh-CN.md) 为准；本文只说明项目内的封装方式，
不复制协议全文。

## 规则层接口

规则构造时接收数据客户端，`evaluate()` 仍只接收时间范围：

```python
class Rule:
    def __init__(self, data_client, config):
        self.data = data_client
        self.config = config

    def evaluate(self, start_time, end_time):
        samples = self.data.get_history(
            tag=self.config["parameters"]["tag"],
            start_time=start_time,
            end_time=end_time,
        )
        return []
```

规则不负责 HTTP、CSV、URL 编码、响应 Header、错误重试、Event cursor 或
`sourceGeneration`。需要窗口开始前状态的状态型规则使用
`data/history_context.py` 的 `get_history_with_previous_sample()`：它先读取
正常范围，再按 30 分钟、2 小时、12 小时、48 小时回溯，最终只保留最近一条
前置样本和 `[start_time, end_time)` 内样本；不会假造默认 `0`。

## `DcsServiceClient`

```python
DcsServiceClient(
    base_url="http://192.168.1.10:8088",
    timeout_seconds=70,
    total_timeout_seconds=120,
    max_retries=4,
    event_page_limit=1000,
)
```

公开方法：

- `health() -> bool`：只表示 HTTP 进程返回了 `status=ok`，不代表 Historian 或 Event Journal 一定可用。
- `get_info(refresh=False) -> ServiceInfo`：获取并缓存服务能力和源时区；也可调用 `refresh_info()`。
- `check_tag(tag) -> TagInfo`：保留 `HistoryTagOK`、`HistoryTagUnknown`、`HistoryTagAmbiguous` 和 `Error` 的服务端语义。
- `get_history(tag, start_time, end_time) -> list[HistorySample]`：读取一个 TAG 的完整 History 范围。
- `get_histories(tags, start_time, end_time) -> dict[str, list[HistorySample]]`：逐 TAG 读取，受客户端实例共享的 `/info` `historyMaxConcurrent` 限制。
- `get_events(start_time, end_time) -> list[DcsEvent]`：读取固定的半开区间 `[start_time, end_time)`。

Base URL、单次请求 timeout 和一次客户端操作的总 timeout 都由构造参数提供，
不写死在规则中。项目没有引入第三方 HTTP 依赖，底层使用 Python 标准库
`urllib`。

## 时间语义

所有传入客户端的 `datetime` 必须是 naive datetime，表示 dcs-service 的源本地
时间，通常是 `China Standard Time`。带 `tzinfo` 的值会抛出
`DcsArgumentError`；客户端不会自动转换 UTC、加 8 小时或删除 offset。

请求时间使用不带 `Z` 或 offset 的 ISO 文本。时间解析接受无小数和 1～7 位小数；
Python 无法保存的第 7 位小数会在解析时明确截断到 microsecond。Event cursor
请求不会从截断后的 `datetime` 重建，而是使用服务返回的原始
`X-DCS-Next-DateTime`，并同时保留 `FracSec` 和 `Ord`。
Event 模型还保留 CSV 中的原始 `DateTime` 文本，用于校验 next cursor，
不把第 7 位精度丢失造成的相等误判带入分页。

## History

`HistorySample` 保留 V1 的全部 10 列。`value` 保持服务端原始文本，数据层不
根据 `DataType` 自动转换成 float、int 或 bool。

每次成功 History 请求校验：

- HTTP 200；
- media type 为 `text/csv`；
- `X-DCS-Tag` 与请求 TAG 一致；
- `X-DCS-Source-TimeZone` 与 `/info` 一致；
- CSV 行数与 `X-DCS-Row-Count` 一致；
- 完整固定 Header Schema。

`history_query_too_large` 映射为 `DcsHistoryQueryTooLargeError`，保留 TAG 和
时间范围，客户端不会立即重复相同请求。

## Event 固定范围分页

`get_events(start_time, end_time)` 的公共语义是固定的 `[start_time, end_time)`，
不是持续同步：

1. 发送 Range 请求 `from`、`to`、`limit`；
2. 读取并校验第一页的 `sourceGeneration`、`HasMore` 和行数；
3. `HasMore=true` 时，只发送 `afterTime`、`afterFracSec`、`afterOrd`、`sourceGeneration`、`limit`；
4. 后续 cursor 页不再发送 `to`，但客户端保留原始 `end_time`；
5. 遇到 `timestamp >= end_time` 的事件时停止读取，并丢弃范围外事件。

每个 Event page 都要求完整的 17 列 Schema 和必要 Header。所有 cursor 页必须
保持同一个 `sourceGeneration`，页内 cursor 严格递增，next cursor 必须等于本页
最后一条事件的 `(DateTime, FracSec, Ord)`；Cursor 页首条事件还必须严格晚于提交
的 cursor。初始 Range 页若出现 `[from, to)` 外事件会抛协议异常；变化或其他完整性
错误时不会返回已读取的部分结果。协议允许空 Cursor page 重复输入 cursor 并以
`HasMore=false` 结束；客户端会校验该例外。`get_events()` 不持久化 cursor、
checkpoint，也不启动后台同步。

## 错误和重试

异常统一继承 `DcsServiceError`，并保留 `status_code`、`code`、`message` 和有
界的诊断上下文。程序分支只使用 HTTP 状态码和 `error.code`，不解析错误文本。

有限重试最多由 `max_retries` 控制，退避为 1、2、4、8 秒并带 jitter，并受
`total_timeout_seconds` 总截止时间限制。会重试：

- `429 service_busy`；
- `503 service_busy`；
- `request_timeout`；
- 临时网络错误。

不会盲目重试：

- 参数错误、404、405、413；
- `source_changed`、`event_cursor_expired`、`retention_gap`、`cursor_ahead`；
- `event_overflow`、`event_journal_full`。

Event 完整性错误属于 fail-closed：调用方会收到异常，而不是空列表或已读取的
部分事件。

## 测试与真实环境 probe

`tests/data/` 使用内存 `HttpResponse` 和 FakeTransport，不访问网络，也不要求
dcs-service 运行。真实服务人工检查使用：

```bash
python experiments/dcs_service/probe.py \
  --base-url http://192.168.1.10:8088 \
  --tag TI-021007_AI1_PV.CV \
  --minutes 5
```

probe 只打印服务信息、TAG 状态以及 History/Event 的行数和首尾时间，不打印
完整数据。
