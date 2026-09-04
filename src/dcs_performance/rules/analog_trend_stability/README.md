# 连续量趋势稳定性考核

`analog_trend_stability` 用于液位、压力、温度、流量、浓度等连续模拟量。规则只发现历史数据中的异常区间并返回 `AssessmentEvent`，班组归属、评分和汇总仍由 Engine 后续阶段处理。

当前生产配置只有 `LICA-012019/PID1/PV.CV` 一个趋势点，但该点
`enabled=false`，因此当前班次不执行趋势稳定性考核、不计分，也不生成该点的趋势
证据图；本说明中的算法和配置字段用于后续启用时的审查。

## 每个 TAG 独立配置

`parameters.points[]` 中的每一个对象都是一个完整的分析单元。它拥有自己的：

- `quality.max_gap_seconds`
- `trend.method`、`trend.alignment`、`trend.window_seconds`、`trend.min_samples`
- `stability` 阈值、持续时间和合并间隔
- 任意数量的 `drift.windows[]`，每个窗口有自己的阈值和持续时间

因此同一规则可以同时配置 30 分钟液位趋势、10 分钟压力趋势和 2 小时温度趋势，参数不会在 TAG 之间共享。

第一阶段正式实现 `rolling_mean`。`centered` 使用 `[t-W/2, t+W/2]`，`trailing` 使用 `[t-W, t]`；窗口按真实时间戳计算，不按固定采样点数计算。

## 指标和事件

稳定性偏差为：

```text
deviation(t) = PV(t) - Trend(t)
```

只有 `abs(deviation) > warning_deviation` 且持续达到 `min_duration_seconds` 才确认事件；期间达到 `high_deviation` 时整段为 `high`。恢复间隔不超过 `merge_gap_seconds` 的异常区间会合并。

漂移窗口的指标为：

```text
delta(t, W) = Trend(t) - Trend(t-W)
```

`t-W` 在相邻趋势点之间时只在同一有效数据段内做线性插值。`delta > 0` 标记为 `up`，`delta < 0` 标记为 `down`。先逐窗口检测，再把同一点、同方向且重叠或足够接近的区间合并为一个 `trend_drift` 事件；`evidence` 保存各窗口的峰值，因此多个窗口不会重复扣分。

## 数据质量和边界

非法或非有限值、Historian hole、CR hole、手工删除、手工插入、时间倒序以及超过 `max_gap_seconds` 的缺口都会切断有效数据段。趋势、插值和事件检测绝不跨段计算。

规则级默认 `assessment_window` 仍必须保持两个 offset 都为 `0`。如果工艺上需要，Result Package 可以在单个点位中覆盖责任窗口偏移；这只改变该点的 `evaluate(start_time, end_time, point_ids=...)` 责任范围，不会用责任窗口偏移伪造趋势预热数据。趋势所需的前后历史仍由规则按照每个 TAG 的参数自行规划。多个 TAG 若所需 `(left_padding, right_padding)` 相同，会共享一次 `get_histories()` 批量读取；不同参数的 TAG 会进入不同查询组。

实际返回的事件总是裁剪到 `evaluate(start_time, end_time, point_ids=...)`：

```text
event.start_time >= start_time
event.end_time <= end_time
```

所以异常跨越班次边界时，每次责任窗口得到自己的时间片，不需要修改 Shift 架构。

## 配置字段

```json
{
  "id": "analog_trend_stability",
  "name": "连续量趋势稳定性考核",
  "enabled": true,
  "assessment_window": {"start_offset_minutes": 0, "end_offset_minutes": 0},
  "parameters": {
    "points": [
      {
        "id": "LICA-012019",
        "history_tag": "LICA-012019/PID1/PV.CV",
        "enabled": true,
        "quality": {"max_gap_seconds": 60},
        "trend": {
          "method": "rolling_mean",
          "alignment": "centered",
          "window_seconds": 1800,
          "min_samples": 30
        },
        "stability": {
          "enabled": true,
          "warning_deviation": 0.08,
          "high_deviation": 0.10,
          "min_duration_seconds": 60,
          "merge_gap_seconds": 20
        },
        "drift": {
          "enabled": true,
          "merge_gap_seconds": 60,
          "windows": [
            {
              "id": "short",
              "window_seconds": 1800,
              "warning_change": 0.05,
              "high_change": 0.08,
              "min_duration_seconds": 120
            }
          ]
        }
      }
    ]
  },
  "scoring": {
    "default_score_per_event": 1,
    "by_point": {
      "LICA-012019": {
        "stability_deviation": {"warning": 1, "high": 2},
        "trend_drift": {"warning": 1, "high": 2}
      }
    }
  }
}
```

`stability` 或 `drift` 可以单独关闭；关闭 `drift` 时可以省略 `windows`，开启时必须至少有一个窗口。评分器根据事件的 `point_id` 和 `score_key`（例如 `trend_drift.high`）查找分值，同时兼容旧的 `by_point.<point_id> = number` 写法。

## 如何增加一个考核点

只需要复制 `parameters.points[]` 中已有的一个对象，修改：

1. `id`
2. `history_tag`
3. `trend`
4. `stability`
5. `drift`

也可以按现场需要修改 `quality` 和该点的评分配置。加入 1 个、10 个或 50 个点都不需要修改 Python 代码。

事件 `data` 至少包含 `point_id`、`history_tag`、`event_type`、`severity`、`score_key`、`duration_seconds`、趋势方法/窗口和稳定的 `event_key`。漂移事件还包含 `direction` 和如下形式的 `evidence`：

```json
[
  {"window_id": "short", "window_seconds": 1800, "peak_change": -0.224},
  {"window_id": "long", "window_seconds": 3600, "peak_change": -0.281}
]
```
