# 持续高报考核

规则 ID：`persistent_high_alarm`。

## 业务定义

规则读取配置中的六个 Historian 点位。正式点位为：

```text
LA-115077  LA-115177  LA-117075
LA-215077  LA-215177  LA-217075
```

对应 Historian 参数均为 `DI1/PV_D.CV`。数字状态 `0` 表示正常，`1` 表示
高报。一次 `0 -> 1` 后连续保持高报超过 300 秒（严格大于 5 分钟）才
生成一次有效事件；连续的多个 `1` 不重复计数，恢复为 `0` 后再次出现
`1` 才开始新的事件。

事件归属始终使用 `alarm_start`，即 `0 -> 1` 的时间。规则不按历史行数
计数，也不按正式班次边界拆分连续高报。

## 输入与配置

每个点位必须提供独立的 `history_tag`。当前正式配置包含六个已确认的完整
Historian TAG，并已使用 `DcsServiceClient.check_tag()` 验证。代码不会把点位
ID 与 `DI1/PV_D.CV` 拼接，也不会在规则中保存 DCS Base URL。

`threshold_seconds`、`active_value` 和 `scoring` 都来自 `config.json`。未知
数字状态会明确抛出解析错误，不会静默变成正常状态。

## 状态机与历史上下文

规则使用 `DcsDataClient.get_history()`。查询结果由通用
`get_history_with_previous_sample()` 补充窗口开始前最近的一条状态，并按
`(timestamp, sequence_no)` 排序去重。找不到前置状态时不会假造初始 `0`：
如果窗口首个观测就是 `1`，该段高报只能在恢复后等待下一次明确的
`0 -> 1`。

状态机只有业务上的 `NORMAL` 和 `ALARM` 两种状态；没有前置样本时内部会
保留一个“起点未知”的保护状态。`ALARM + 1` 保持原事件，`ALARM + 0`
结束原事件。

## 窗口、跨班与 OPEN 事件

正式班次仍是 `08:00-20:00` 或 `20:00-次日 08:00`。规则责任窗口使用：

```text
start = shift.start - 10 minutes
end   = shift.end - 10 minutes
```

为判断窗口末尾的报警，Historian 查询结束时间扩展为
`window.end + threshold + 1 second`。因此尚未恢复但已确认超过阈值的高报
会生成 OPEN `AssessmentEvent`：其 `end_time` 是当前已确认的查询观察终点，
`data["alarm_end"]` 为 `None`，`data["is_open"]` 为 `True`。

对 OPEN 事件，Rule 会从首次查询已经覆盖的 cursor 继续按不重叠分段向后查找
第一次恢复状态 `0`。默认累计搜索边界为 30 分钟、2 小时、12 小时和 48 小时；
找到恢复时补写真实 `alarm_end` 和 `duration_seconds`，48 小时内找不到时保留
OPEN，并将观察时长延伸到配置的搜索上限。

最终事件只保留 `window.start <= alarm_start < window.end`。所以窗口外读取的
上下文只用于判断状态，不能让上一班的事件被下一班再次返回；跨正式
`20:00` 的一次连续高报也不会拆分。

精确边界：持续 `299.999` 秒和 `300.000` 秒不考核，`300.001` 秒考核。

## 失败行为

缺少数据客户端、点位、TAG、阈值或有效数字状态都会明确失败。DCS 数据层
抛出的 `DcsServiceError` 和数据完整性错误不会被规则捕获为“空事件”。
