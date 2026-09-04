# 组件粘度趋势控制

`component_viscosity_control` 使用 `PI-2311001/AI1/PV.CV` 作为组件粘度趋势代理指标。
规则本身不修改现有的 `analog_limit_exceedance`，由本目录的 `config.json` 独立启用。

## 算法

1. 原始 Historian 值按固定1分钟桶取中位数。
2. 对连续10个一分钟桶计算10分钟尾部平均；窗口不完整或存在缺桶时，不生成指标值。
3. 计算连续1小时指标窗口的极差（最大值减最小值）；极差超过1 MPa时标记为扰动候选，并以窗口内最大的相邻跳变作为扰动开始；相邻间隙不超过10分钟的候选段合并。
4. 从扰动开始连续剔除2小时。剔除仅影响本规则的考核值，原始数据和剔除窗口仍保留在事件数据中。
5. 在剔除后的指标上执行上下限判定：低于15.95为粘度趋势偏低，高于16.25为粘度趋势偏高。
6. 单方向超限持续超过600秒确认一次事件；超限之间间隙不超过600秒时合并为同一次事件。
7. 一个连续异常始终只形成一个 `AssessmentEvent`。事件达到
   `min_duration_seconds` 时产生第1个 `penalty unit`；启用重复考核后，每完整持续
   一个 `repeat_penalty.interval_seconds` 再增加1个 unit。

Historian 的质量标志、非 Good 的 `DeltaVStatus`、非有效的 `ArchiveStatus`、非法数值、
手工插入/删除和时间缺口都会切断有效指标段。普通数据段首个异常点继续保持未知状态；
只有明确位于扰动剔除窗口之后的首个有效指标段才从该点重新起算。

事件返回为通用 `AssessmentEvent`，事件类型为 `viscosity_low` 或 `viscosity_high`，并携带聚合、平滑、上下限和剔除窗口配置，便于审计。

## 配置

当前示例配置只包含一个点位：

- `history_tag`: `PI-2311001/AI1/PV.CV`
- 一分钟中位数：`aggregation.bucket_seconds = 60`
- 十分钟尾部平均：`smoothing.window_seconds = 600`
- 目标值：`assessment.target = 16.05`
- 范围：`assessment.low_limit = 15.95`、`assessment.high_limit = 16.25`
- 持续时间：`assessment.min_duration_seconds = 600`
- 合并间隙：`assessment.merge_gap_seconds = 600`
- 持续追加扣分：`assessment.repeat_penalty.enabled = true`
- 追加间隔：`assessment.repeat_penalty.interval_seconds = 1800`（30分钟）
- 次数上限：`assessment.repeat_penalty.max_units = null`（不限；该值包含首次扣分）
- 扰动判定：连续1小时极差 `max(metric) - min(metric) > 1.0 MPa`
- 扰动剔除：`exclusion.remove_after_start_seconds = 7200`
- 考核窗口：上班后 1 小时开始，至下个班上班前 1 小时结束
- 每个 `viscosity_low` 或 `viscosity_high` penalty unit 计 2 分；最终分数由通用
  scorer 按基础分乘 `score_multiplier` 得出

一分钟中位数是本规则固定算法的一部分，`aggregation` 不再配置 `enabled` 开关。

当前生产配置中该规则 `enabled=true`，`PI-2311001` 点位也处于启用状态。

## 持续异常重复考核

事件和扣分次数是两个概念：连续异常不会被拆成多个事件，重复扣分只记录在同一个
事件的 `data.penalty` 中。`repeat_penalty` 缺失或 `enabled=false` 时兼容旧行为，
一个合格事件只产生1个 unit。

```json
"repeat_penalty": {
  "enabled": true,
  "interval_seconds": 1800,
  "max_units": null
}
```

首次 checkpoint 位于异常开始后 `min_duration_seconds`，之后每完整达到一个追加间隔
产生一个 checkpoint。边界按“达到才计入”处理：599秒为0次，600秒为1次，2399秒
为1次，2400秒为2次。`max_units` 表示单个连续事件的总次数，包括第一次。

短暂恢复不会由 scoring 重新识别：恢复时间不超过 `merge_gap_seconds` 时，沿用
detector 的同一事件，penalty 计时不清零；恢复时间超过该间隔时，detector 产生新
事件，新事件从第一次 checkpoint 重新计算。跨班事件归属本阶段保持现状不变，但
每个事件会提前输出基于时间戳的 `penalty.checkpoints`，供后续按责任窗口分配。

例如一个低粘度事件从22:00持续到02:10（250分钟），首次阈值10分钟、重复间隔30
分钟，则仍然只有 `Events = 1`，但有9个 checkpoint；单次基础分为2时，最终分数为
`2 × 9 = 18`。

事件数据会包含以下审计字段：

```json
{
  "penalty": {
    "enabled": true,
    "initial_threshold_seconds": 600,
    "repeat_interval_seconds": 1800,
    "max_units": null,
    "units": 9,
    "checkpoints": ["2026-09-03T22:10:00", "2026-09-03T22:40:00"]
  },
  "score_multiplier": 9
}
```

可视化仍以一整段阴影表示一次连续异常，并用浅色虚线标记 penalty checkpoints；图
标题和 metadata 额外显示 `Penalties`、`penalty_unit_count` 和
`penalty_checkpoint_count`，不会把一个长事件画成多个事件。
