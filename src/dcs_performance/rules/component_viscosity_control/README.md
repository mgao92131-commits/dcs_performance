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
- 扰动判定：连续1小时极差 `max(metric) - min(metric) > 1.0 MPa`
- 扰动剔除：`exclusion.remove_after_start_seconds = 7200`
- 考核窗口：上班后 1 小时开始，至下个班上班前 1 小时结束
- 每次确认的 `viscosity_low` 或 `viscosity_high` 事件计 2 分

一分钟中位数是本规则固定算法的一部分，`aggregation` 不再配置 `enabled` 开关。

当前生产配置中该规则 `enabled=true`，`PI-2311001` 点位也处于启用状态。
