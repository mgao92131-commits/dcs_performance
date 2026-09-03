# 浆料进浆量和酯化液位考核方案

本目录用于浆料进浆量与酯化液位考核方案的试验数据和分析。

点位：

- `LOGIC27/YK-TLFH/OUT1.CV`
- `SY-116/AI1/PV.CV`
- `SY-216/AI1/PV.CV`
- `LICA-012019/PID1/PV.CV`

## 获取历史数据

`fetch_history.py` 默认获取执行时刻往前 36 小时的 `[start, end)` 历史区间：

```powershell
$env:NO_PROXY = "192.168.1.10,127.0.0.1,localhost"
$env:no_proxy = $env:NO_PROXY
$env:PYTHONPATH = "src"
python experiments/浆料进浆量和酯化液位考核方案/fetch_history.py
```

也可以用 `--to 2026-09-02T15:00:00` 固定结束时刻，以便复现实验数据。
CSV 中保留 Historian 状态字段；`download_manifest.json` 记录实际查询区间、文件名和行数。

## 生成考核方案和不符合点清单

当前正式试验规则直接使用DCS已经修正后的LOGIC27，不再除以1.0099：

- 酯化液位先做60秒尾随滑动平均；绝对上下限为71～73；
- 酯化液位2小时速率超过±0.14液位/小时并连续2小时，生成速率事件；
- LOGIC27与SY116+SY216分别做60秒尾随滑动平均，差值超过±15并连续5分钟，生成流量平衡事件；
- 不再进行液位触发后的LOGIC27调节响应考核。

运行规则扫描：

```powershell
$env:PYTHONPATH = "src"
python experiments/浆料进浆量和酯化液位考核方案/assess_control_rules.py --data-dir experiments/浆料进浆量和酯化液位考核方案/validation_7d
```

结果默认写入 `validation_7d/assessment_outputs_rule_v2/`：

- `assessment_report.md`：两项主指标的规则说明和结果摘要；
- `assessment_summary.csv`：液位上下限、液位速率、浆料流量平衡三类事件摘要；
- `assessment_events.csv`：逐次连续超限事件及完整参数；
- `assessment_parameters.json`：本次扫描使用的阈值、平滑窗口和持续时间。
