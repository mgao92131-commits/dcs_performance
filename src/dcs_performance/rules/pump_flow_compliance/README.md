# pump_flow_compliance

`pump_flow_compliance` 是按泵组独立配置的泵组流量考核规则。当前包含
`117P01`、`115P05` 和 `115P03` 三个点位；每个点位分别配置 A 泵、B 泵、流量
TAG、正常最低流量、切泵最低流量和最大切泵时间。

泵状态由 A/B 两个数字量重建：

- `1/0`：`NORMAL_A`
- `0/1`：`NORMAL_B`
- `1/1` 或 `0/0`：`SWITCHING`

同一时间戳的多个 Historian 记录会先按 TAG 内 `sequence_no` 应用，再用最终
A/B 组合执行一次状态迁移。因此同刻停泵和启泵会被识别为一个瞬时切换，不会
制造假的中间区间。

规则使用窗口前 `max_switch_duration_seconds` 的历史上下文和窗口后的同等
观察尾部。切泵超时采用严格 `duration > max_switch_duration_seconds`：恰好
600 秒不超时，600.001 秒才产生 `switch_timeout`。

产生的事件相互独立：

- `low_flow`：实际流量低于当前模式的动态最低流量；连续低流量只产生一次。
- `switch_timeout`：一次切泵超过该点位自己的最长允许时间。事件从
  `switch_start + max_switch_duration_seconds` 开始归属责任窗口。

切泵超过最大时间后仍继续使用 `switching_min_flow`，所以 `low_flow` 与
`switch_timeout` 可以同时产生。初始泵状态、流量或上下文不足时保持未知，
规则不会把缺失值猜成停止或零流量。

责任窗口和班组由公共 `AssessmentWindow`、`Shift` 与后续 assignment/scoring
阶段处理；本规则不识别班组，也不包含 Engine 专用分支。当前正式配置使用公共
Scorer 的 `default_score_per_event`，并将 `low_flow` 计 1 分、`switch_timeout`
计 2 分。
