# Result Package

Result Package 是一次完整班次考核运行的原子交付物，同时包含机器可读的
`result.json` 和每个启用考核点的一张静态 PNG 证据图。

## 目录结构

```text
output/
└── 20260903T080000_20260903T200000_A/
    ├── result.json
    └── images/
        ├── analog_limit_exceedance__LICA-012019.png
        └── level_rate_compliance__LICA-012019.png
```

运行身份由最终解析出的班次起止时间和 `team_id` 构成，不假设班次时长。文件名
只保留 `A-Z a-z 0-9 - _ .`，其他字符替换为 `_`；JSON 始终保存原始业务 ID。
考核项目的唯一身份是 `(rule_id, point_id)`，不同规则中的同名点不会合并或覆盖。
若两个不同业务身份清洗后仍产生同名文件，后一个文件会附加稳定的十六进制摘要，
确保每个点仍有独立 PNG；JSON 中的相对路径始终指向实际文件。

## JSON V1

顶层 `schema_version` 固定为 `"1.0"`。文档包含 `run`、`time_basis`、`shift`、
`summary` 和 `rules[]`；每条规则包含规则级默认 assessment window 与 `points[]`；每个点
包含实际生效的 `assessment_window`、`status`、`data_status`、score、PNG 相对路径和
`events[]`。点位窗口由规则默认值和点位配置合并得到。

JSON 使用 UTF-8、`ensure_ascii=False`、`indent=2` 和 `allow_nan=False`。datetime
递归转换为 ISO 8601；未知业务对象会导致明确错误，不使用 `default=str`。文档不
保存 Historian 原始时间序列。事件 score 原样来自现有 `AssessmentScorer`。

`status` 只由事件数量决定：有事件为 `violation`，否则为 `normal`。`data_status`
支持 `ok`、`partial` 和 `no_data`。多 TAG 考核点只有部分必需 TAG 包含有效数据时
为 `partial`；无有效历史数据仍生成带 “No valid history data” 提示的 PNG。网络、
协议或绘图异常则使整次交付失败。

图片 X 轴固定为该点的实际 assessment window，并在窗口超出正式班次时标记班次
边界。平滑、速率、趋势与状态重建所需的前后文按对应规则的查询规划读取，因此
窗口边缘的派生曲线与规则检测使用同一处理逻辑；这些上下文样本不会写入 JSON。

## CLI

```bash
dcs-performance run \
  --at 2026-09-03T13:00:00 \
  --output ./output
```

可选参数为 `--rules-dir`、`--service-url` 和 `--overwrite`。`--at` 必须是无时区
offset 的本地 ISO datetime；班次由现有生产排班配置和
`Cyclic12HourShiftCalendar.shift_for_timestamp()` 解析。

正式目录默认不覆盖。所有 PNG 先写入隐藏临时目录，全部成功后才严格序列化并
原子写入 `result.json`，最后发布整个运行目录。覆盖模式也先完整生成新包再替换
旧包；生成失败不会破坏旧的成功结果。

同一个 `run_id` 只支持一个写入者。如果覆盖发布因进程崩溃或主机断电而中断，
下一次同 `run_id` 运行会检查遗留的 `.backup-*` 和 `.tmp-*`。正式目录缺失且只有
一个 backup 时自动恢复；存在多个 backup、无法唯一判断时明确失败，不猜测恢复。
