# 连续量上下限超限考核

`analog_limit_exceedance` 对每个配置的 Historian 连续量 TAG 独立执行上下限考核。

## 业务定义

对于一个点：

- `PV < low.limit` 是低限超限；
- `PV > high.limit` 是高限超限；
- `low.limit <= PV <= high.limit` 是正常值。

因此，恰好等于上下限的值本身不会触发超限。

每个点的 `low`、`high` 可以独立启用，并且各自拥有自己的 `limit`、
`min_duration_seconds` 和 `merge_gap_seconds`。

## 时间规则

只有当同方向超限的持续时间严格满足：

```text
duration_seconds > min_duration_seconds
```

才生成一次 `AssessmentEvent`。例如最小持续时间为 300 秒时，300 秒不考核，
301 秒才考核。这与 `persistent_high_alarm` 中“超过 N 分钟”的语义一致。

超限开始时间使用第一个实际观察到越过阈值的 Historian 样本时间，不进行线性插值。
例如 10:00 的值为 119、10:05 的值为 121、上限为 120，开始时间是 10:05。

事件结束时间使用第一个观察到恢复正常（或进入另一方向超限）的样本时间。查询观察尾部
仍处于超限时，事件是 open occurrence：`AssessmentEvent.end_time` 使用观察结束时间，
而 `event.data["violation_end"]` 保持 `None`。

## 短暂恢复与合并

同一个方向的两段超限之间，如果恢复正常的间隔满足：

```text
gap <= merge_gap_seconds
```

则视为一次连续责任事件。合并事件的开始时间取第一段开始时间，结束时间取最后一段
结束时间；持续时间使用两者的完整时间跨度，因此短暂恢复区间包含在合并后的持续时间中。

只合并相同 `event_type`。高限和低限永远不会合并，即使二者在同一时间戳发生状态切换。

高限事件记录事件期间的最大 PV，低限事件记录事件期间的最小 PV，并记录首次达到该极值
的样本时间。极值只用于事件解释和审查，当前版本不根据极值幅度改变分数。

## 窗口、跨班与初始状态

Rule 会通过共享的 `history_context` 查询考核窗口前最近一个样本，用它初始化窗口开始时的
状态。窗口前已经开始的高限或低限事件不会在新窗口重新生成；事件归产生它的责任窗口，
不按班次边界拆分。责任归属采用半开区间 `[window_start, window_end)`：窗口开始时刻属于
当前窗口，窗口结束时刻不属于当前窗口。

如果没有窗口前样本，初始状态为 UNKNOWN。第一条已经处于超限的样本不能被武断地当作
窗口开始的新事件；必须先观察到正常值或另一种确定状态，之后的真实状态边沿才可以提供
可靠的开始时间。

## 配置示例

完整示例见本目录的 `config.json`。核心结构如下：

```json
{
  "id": "analog_limit_exceedance",
  "name": "连续量上下限超限考核",
  "enabled": true,
  "parameters": {
    "points": [
      {
        "id": "TI-013008",
        "history_tag": "TI-013008/AI1/PV.CV",
        "enabled": true,
        "low": {
          "enabled": true,
          "limit": 80.0,
          "min_duration_seconds": 300,
          "merge_gap_seconds": 20
        },
        "high": {
          "enabled": true,
          "limit": 120.0,
          "min_duration_seconds": 300,
          "merge_gap_seconds": 20
        }
      }
    ]
  },
  "scoring": {
    "default_score_per_event": 1,
    "by_point_event_type": {
      "TI-013008": {"low_limit": 1, "high_limit": 1}
    }
  }
}
```

## 可选的向后滑动平均

点位可以配置可选的 `smoothing`。启用后，规则对向后时间窗口内的 PV
计算平均值，再用平均曲线执行上下限判断。时刻 `t` 只使用
`[t-window_seconds, t]` 的样本，不读取未来数据；规则会自动查询责任窗口前的
预热数据。

```json
"smoothing": {
  "enabled": true,
  "method": "trailing_mean",
  "window_seconds": 30,
  "min_samples": 10
}
```

没有配置 `smoothing` 的点位继续使用原始 PV。事件数据会记录实际使用的平滑方法、
窗口和最小样本数，便于审计。

`LIC-117016/PID1/PV.CV` 和 `LIC-217016/PID1/PV.CV` 均使用 60 分钟后向滑动平均
（至少 30 个样本），平滑值的正常区间为 38.5 至 39.5。低于 38.5 或高于
39.5 持续超过 5 分钟生成超限事件。

## 当前不支持

当前版本不支持 hysteresis、upper/lower recovery limit、按持续时间阶梯计分、每 N 分钟重复
扣分、按超限幅度计分、动态上下限（包括根据设定值动态改变上下限）、基于其他设备状态启停
规则、数据库持久化、Web/API、定时运行、人工豁免时间、设备运行状态联动、生产阶段联动，
也不按班次拆分跨班事件。
