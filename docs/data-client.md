# DCS 数据访问层

`dcs_performance` 的规则层只依赖 `DcsDataClient`，由
`DcsServiceClient` 负责访问 dcs-service 的完整范围流式 CSV 协议。默认 Base URL
是：

```text
http://192.168.1.10:8088
```

可通过环境变量 `DCS_SERVICE_BASE_URL` 覆盖。客户端只访问 dcs-service HTTP
API，网络转发细节对规则和数据模型透明。协议细节以
[`API-ACCESS.zh-CN.md`](API-ACCESS.zh-CN.md) 为准。

## 规则层接口

公共接口保持稳定：

```python
class DcsDataClient:
    def get_history(self, tag, start_time, end_time): ...
    def get_histories(self, tags, start_time, end_time): ...
    def get_events(self, start_time, end_time): ...
```

规则只接收源时区的 naive `datetime`，不负责 HTTP、URL 编码、CSV、响应 Header、
重试或 Event checkpoint。`get_history()` 返回一段完整的 `HistorySample` 列表，
`get_events()` 返回完整 `[start_time, end_time)` 范围的 `DcsEvent` 列表。

## `DcsServiceClient`

```python
from dcs_performance.data import DcsServiceClient

client = DcsServiceClient(
    base_url="http://192.168.1.10:8088",
    timeout_seconds=70,
    total_timeout_seconds=None,
    max_retries=4,
)

history = client.get_history("TAG1", start_time, end_time)
events = client.get_events(start_time, end_time)
```

`total_timeout_seconds` 默认是 `None`，表示不设置整个 CSV 流的硬截止时间；单次
连接和读取仍受 `timeout_seconds` 约束。`get_histories()` 继续按 TAG 发起请求，
并通过 `/api/v1/info` 的 `historyMaxConcurrent` 控制同一客户端实例的并发。
Event 请求同理遵守 `eventMaxConcurrent`。

## 完整范围流

History 请求只发送 `tag`、`from`、`to`，一个业务范围只发一个 HTTP 请求。Event
请求只发送 `from`、`to`，也只发一个 HTTP 请求。服务端内部的
`historyStreamWindowMinutes` 和 `eventStreamWindowMinutes` 是实现细节，客户端不
据此切片；尤其不会固定按 24 小时拆 History。

CSV parser 从 HTTP 文本流逐行读取固定 Schema，只有在响应正常读到 EOF 后才把
列表返回。HTTP 200、已收到部分 CSV 不等于成功。

如果流在 EOF 前中断，数据层会抛出 `DcsIncompleteStreamError`，丢弃该次已解析的
所有记录，并按重试策略重新请求完整的原始范围。规则层不会看到 partial
History/Event，也不从中断位置续传。

## History 上下文搜索

需要窗口开始前状态的规则使用：

```python
from dcs_performance.data.history_context import (
    get_history_with_previous_sample,
)
```

该 helper 先读 `[start_time, end_time)`，再按业务配置的 30 分钟、2 小时、12 小时、
48 小时累计 horizon 回溯，最多保留一条最新前置样本。向后寻找状态变化时使用
`find_next_sample()`；搜索 horizon 可以直接覆盖 24 小时以上，数据访问层不会为
了废弃的服务端大小假设再次切段。

没有前置样本时保持未知状态，不用 `0` 等默认值伪造状态。数据客户端异常和
predicate 异常会继续向调用方传播。

## Event Cursor

`EventCursor` 仅表示 Event 增量同步 checkpoint，包含 `DateTime`、`FracSec` 和
`Ord`（以及可选的原始时间文本）。它不是 `get_events()` 的分页游标；当前考核
查询不会内部调用 Cursor，也不读取分页 Header。若以后实现增量同步，Cursor 请求
必须同时发送 `afterTime`、`afterFracSec`、`afterOrd`、`sourceGeneration` 和
`to`。

## 模型和错误

`HistorySample` 保留协议十列，`value` 保持原始文本；`DcsEvent` 保留协议十七列
以及 `timestamp_raw`。固定范围 Event 分页状态机已经移除。

错误统一继承 `DcsServiceError`。协议/Schema、范围、顺序和数据完整性错误采用
fail-closed 语义，不返回空列表掩盖问题。

## 测试与真实服务

`tests/data/` 使用内存响应和 fake transport，不访问真实 DCS。完整测试运行：

```bash
pytest
```

真实环境人工检查使用：

```bash
python experiments/dcs_service/probe.py \
  --base-url http://192.168.1.10:8088 \
  --tag "<REAL_TAG>" \
  --from 2026-08-31T00:00:00 \
  --to 2026-08-31T02:00:00
```

probe 会打印 Health、Info、TAG、History/Event 行数和首尾时间（Event 还包括
`FracSec`、`Ord`），但不会把真实数据写入测试结果。
