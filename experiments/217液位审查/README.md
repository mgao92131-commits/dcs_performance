# 217液位审查

本目录参照 `experiments/117液位审查`，用于审查
`LIC-217016/PID1/PV.CV` 的实际值趋势和正式上下限超限事件。

## 趋势分析

脚本默认获取执行时刻前 72 小时数据，按 1 分钟聚合后计算 15、30、60
和 120 分钟后向滑动均值，输出 CSV、PNG 和运行记录：

```powershell
$env:PYTHONPATH = "src"
python experiments/217液位审查/fetch_and_plot_lic_217016.py
```

可用 `--to 2026-09-03T17:00:00` 固定截止时间以便复算，也可用 `--hours`
指定趋势窗口。

## 运行正式超限规则

该命令直接加载正式
`src/dcs_performance/rules/analog_limit_exceedance/config.json` 中的
`LIC-217016` 配置，默认分别运行最近 72 小时和 48 小时：

```powershell
$env:PYTHONPATH = "src"
python experiments/217液位审查/run_lic_217016_rule.py
```

输出为两个时间范围各自的事件 CSV 和完整 JSON 审计摘要。
