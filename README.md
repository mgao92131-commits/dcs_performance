# dcs_performance

`dcs_performance` 是一个用于基于 DCS 历史数据和事件数据开展规则化绩效考核的 Python 项目。

当前仓库包含一套可配置的班次级考核执行链，以及七个正式考核规则目录：
`analog_limit_exceedance`、`analog_trend_stability`、`component_viscosity_control`、
`flow_balance_compliance`、`level_rate_compliance`、`persistent_high_alarm` 和
`pump_flow_compliance`，另有一个不访问数据的 `example_rule` 接口样例。规则不直接
连接 DeltaV/DCS 数据库，而是通过只读 dcs-service V1 HTTP API 获取 DCS 数据。

## 核心职责分离

| 模块 | 职责 |
| --- | --- |
| `Rule` | 接收一段时间范围，返回这段时间内需要考核的 `AssessmentEvent` |
| `Rule config` | 保存规则名称、启用状态、考核时间偏移、阈值/参数和评分配置 |
| `Shift` | 表示已经确定的“谁在什么时间上班” |
| `AssessmentWindow` | 将班次、规则默认值和点位覆盖转换为传给 `Rule.evaluate()` 的实际时间范围 |
| `ShiftResolver` / `ShiftCalendar` | 根据配置计算班次并解析时间所属的班组/班次 |
| `Assignment` | 将考核事件归属到班组/班次；有责任窗口的规则使用执行它的正式 `Shift` |
| `Scoring` | 从规则配置读取点位/默认积分，生成 `AssignedAssessmentEvent` |
| `Summary` | 按正式班次、责任窗口和点位汇总事件次数与积分 |
| `Engine` | 加载启用规则、计算考核窗口、执行规则并保留详细执行上下文 |

规则本身不知道 A/B/C 班、白班、夜班或三班两倒。所有规则统一使用以下接口：

```python
events = rule.evaluate(start_time, end_time, point_ids=None)
# events: list[AssessmentEvent]
```

`point_ids=None` 表示执行全部 enabled points；显式传入点 ID 集合时，规则只读取和计算
这些点。Runner 会按每个点的 effective assessment window 分组，同一窗口的点合并为一次
`evaluate(..., point_ids=[...])` 调用，不同窗口分别调用。未知或 disabled point ID 会
明确失败，空集合直接返回空事件且不读取 Historian。

`AssessmentEvent` 只描述问题发生的起止时间、消息和规则相关数据，不包含班组或得分。

## 规则目录约定

每条考核规则都是独立目录，规则下的全部考核点配置跟随该规则存放：

```text
src/dcs_performance/rules/<rule_id>/
    rule.py
    config.py              # 需要强校验的规则可提供配置模型
    detector.py             # 规则的纯业务检测器（如适用）
    config.json
    README.md
```

`RuleLoader` 按目录自动发现 `rule.py` 和 `config.json`，再根据配置中的 `enabled`
决定是否执行；不存在集中维护所有规则参数的 `rules.json`。`example_rule` 是一个
不访问数据、始终返回空事件列表的接口样例，当前配置仍为启用状态。

## 生产责任排班

生产考核使用独立的 `Cyclic12HourShiftCalendar`，不修改
`ThreeTeamTwoShiftCalendar`。配置文件是
[`performance_schedule.json`](src/dcs_performance/shifts/performance_schedule.json)：

```json
{
  "reference_start": "2026-08-31T08:00:00",
  "shift_hours": 12,
  "rotation": ["B", "C", "A"],
  "team_names": {"A": "甲班", "B": "乙班", "C": "丙班"}
}
```

基准时刻为乙班，之后每 12 小时按乙、丙、甲循环；基准之前使用同一个公式
反向计算。`Shift` 仍表示正式的 `08:00-20:00` 或 `20:00-次日 08:00`
班次，内部逻辑只使用稳定 ID `A/B/C`。

## 标准三班两倒排班

排班配置位于 JSON 文件中。`reference_date` 只表示轮换数组第 0 项对应的日期；它
必须由生产部署者替换为已经确认的实际基准日期，示例文件中的日期不是生产基准。
当前只支持 A/B/C 三个班组、08:00-20:00 白班和 20:00-次日 08:00 夜班。

```python
from datetime import datetime
from pathlib import Path

from dcs_performance.shifts import (
    CalendarShiftResolver,
    ThreeTeamTwoShiftCalendar,
    load_schedule_config,
)

config = load_schedule_config(
    Path("src/dcs_performance/shifts/schedule.example.json")
)
calendar = ThreeTeamTwoShiftCalendar(config)
resolver = CalendarShiftResolver(calendar)

# 这是示例配置下的演示日期，不代表生产现场的实际班组。
shift = resolver.resolve(datetime(2026, 9, 10, 14, 0))
print(shift.team_id, shift.shift_type, shift.start_time, shift.end_time)
```

`calendar.get_shifts(start_time, end_time)` 返回与查询范围相交的全部班次，使用
`shift.start_time < end_time and shift.end_time > start_time` 判断交集。夜班归属于
它开始的自然日，所有时间区间都采用 `[start_time, end_time)`。

当前排班模块只处理固定标准排班，不支持加班、调班、替班、临时班次、节假日特殊
班次或人工覆盖排班。

## 当前规则

`RuleLoader.list_metadata()` 当前发现以下规则目录。是否执行由各目录的
`config.json` 中的 `enabled` 控制：

当前生产配置共有 35 个启用考核点；完整的点位、范围、持续时间、评分和考核窗口见
[`docs/assessment-points.md`](docs/assessment-points.md)。

| 规则 ID | 名称 | 默认状态 | 当前用途 |
| --- | --- | --- | --- |
| `analog_limit_exceedance` | 连续量上下限超限考核 | 启用 | 连续量上下限超限 |
| `analog_trend_stability` | 连续量趋势稳定性考核 | 启用 | 趋势偏差和趋势漂移 |
| `component_viscosity_control` | 组件粘度趋势控制 | 启用 | 粘度趋势上下限 |
| `example_rule` | 示例考核规则 | 启用 | 空事件接口样例 |
| `flow_balance_compliance` | 浆料进料量平衡考核 | 启用 | Logic 与 SY 总流量偏差 |
| `level_rate_compliance` | 酯化液位变化速率考核 | 启用 | 液位变化速率 |
| `persistent_high_alarm` | 持续高报考核 | 启用 | 数字量持续高报 |
| `pump_flow_compliance` | 泵组流量考核 | 启用 | 泵组低流量和切泵超时 |

### 持续高报考核

规则目录见
[`persistent_high_alarm`](src/dcs_performance/rules/persistent_high_alarm/README.md)。
它从配置的六个 Historian TAG 读取数字状态：`0` 为正常，`1` 为高报，当前点位为：

```text
LA-115077  LA-115177  LA-117075
LA-215077  LA-215177  LA-217075
```

一次 `0 -> 1` 连续保持超过 300 秒才生成一次事件；严格 300 秒不计，恢复为
`0` 后再次高报才开始新事件。默认责任窗口为班次开始前 10 分钟至班次结束前
10 分钟，即白班 `07:50-19:50`、夜班 `19:50-次日 07:50`。跨正式班次的连续高报
不按边界拆分，事件按高报开始时间归属。

### 连续量上下限超限考核

规则目录见
[`analog_limit_exceedance`](src/dcs_performance/rules/analog_limit_exceedance/README.md)。
每个 TAG 独立配置低限和高限：`PV < low.limit` 为低限超限，
`PV > high.limit` 为高限超限，等于限值属于正常。当前配置包含：

| 点位 | Historian TAG | 低限 | 高限 | 最小持续时间 |
| --- | --- | ---: | ---: | ---: |
| `LICA-012019` | `LICA-012019/PID1/PV.CV` | 71 | 73 | 300 秒 |
| `EU-II-217R011` | `EU-II-217R011/AI1/PV.CV` | 82.5 | 85.5 | 300 秒 |
| `EU-II-117R011` | `EU-II-117R011/AI1/PV.CV` | 81.5 | 84.5 | 300 秒 |
| `WIC-011006` | `WIC-011006/PID1/PV.CV` | 69.4 | 69.6 | 300 秒 |
| `LIC-011007` | `LICA-011007/PID1/PV.CV` | 83.0 | 85.0 | 300 秒 |
| `LIC-013107` | `LIC-013107/PID1/PV.CV` | 30.0 | 75.0 | 300 秒 |
| `LIC-017149` | `LICA-017149/PID1/PV.CV` | 36.0 | 44.0 | 300 秒 |
| `LIC-117016` | `LIC-117016/PID1/PV.CV` | 38.5 | 39.5 | 300 秒 |
| `LIC-217016` | `LIC-217016/PID1/PV.CV` | 38.5 | 39.5 | 300 秒 |
| `TIC-013060` | `TIC-013060/PID1/PV.CV` | 110.0 | 125.0 | 1800 秒 |
| `LIC-013065` | `LIC-013065/PID1/PV.CV` | 62.0 | 68.0 | 600 秒 |
| `TIC-012022` | `TIC-012022/PID1/PV.CV` | 288.3 | 289.7 | 600 秒 |
| `TIC-015009` | `TIC-015009/PID1/PV.CV` | 289.3 | 290.7 | 600 秒 |
| `TIC-117117` | `TIC-117117/PID1/PV.CV` | 297.3 | 298.7 | 600 秒 |
| `TIC-217117` | `TIC-217117/PID1/PV.CV` | 297.3 | 298.7 | 600 秒 |
| `TIC-117001` | `TIC-117001/PID1/PV.CV` | 280.3 | 281.7 | 600 秒 |
| `TIC-217001` | `TIC-217001/PID1/PV.CV` | 280.3 | 281.7 | 600 秒 |
| `TIA-023052` | `TIA-023052/AI1/PV.CV` | 110.0 | 140.0 | 3600 秒 |
| `TI-011003` | `TI-011003/AI1/PV.CV` | 90.0 | 100.0 | 600 秒 |
| `VIT-118020` | `VIT-118020/AI1/PV.CV` | 0.641 | 0.643 | 300 秒 |

只有持续时间严格大于各方向的 `min_duration_seconds` 才生成事件；短暂恢复可按
`merge_gap_seconds` 合并。事件记录超限方向、持续时间和极值，评分由
`scoring.by_point_event_type` 配置，当前示例点位 `LICA-012019` 的低限/高限分值为 3/3。
`EU-II-217R011` 和 `EU-II-117R011` 从班次开始后 4 小时起考核至班次结束，低限/高限
分值均为 2/2；每个点本班次多次合格超限只计一次。`WIC-011006` 的低限/高限分值为
2/2，正常范围为 69.4～69.6。
`LIC-011007` 的低限/高限分值为 2/2，正常范围为 83.0～85.0。

完整的 20 个连续量点位清单、平滑参数、逐点考核窗口和各规则的全部考核点，见
[`docs/assessment-points.md`](docs/assessment-points.md)。其中
`EU-II-217R011`、`EU-II-117R011` 的考核从班次开始后 4 小时起，其他连续量点沿用
整个班次窗口；表中最小持续时间按各点配置执行。

### 连续量趋势稳定性考核

规则目录见
[`analog_trend_stability`](src/dcs_performance/rules/analog_trend_stability/README.md)。
当前配置针对 `LICA-012019/PID1/PV.CV`，使用 `rolling_mean` 趋势，同时支持：

- 稳定性偏差：按 warning/high 阈值和持续时间生成事件；
- 趋势漂移：按多个时间窗口比较趋势变化，合并同点同方向的结果；
- 数据质量保护：非法值、Historian/CR hole、手工删除/插入和过大时间缺口会切断
  有效数据段。

评分使用事件的 `point_id` 和 `score_key`，不在规则代码中写死。

### 组件粘度趋势控制

规则目录见
[`component_viscosity_control`](src/dcs_performance/rules/component_viscosity_control/README.md)。
当前启用点为 `PI-2311001/AI1/PV.CV`：原始值按 1 分钟取中位数，再作 10 分钟尾随
平均；目标值为 16.05，正常范围为 15.95～16.25。异常趋势连续超过 600 秒产生一个
`viscosity_low` 或 `viscosity_high` 事件，首次确认后每 1800 秒追加一个 penalty unit，
每个 unit 2 分。考核窗口为上班后 1 小时至下个班上班前 1 小时，扰动区间按配置排除；
事件和 penalty units 分开记录，详情见规则 README。

### 浆料进料量平衡考核

规则目录见
[`flow_balance_compliance`](src/dcs_performance/rules/flow_balance_compliance/README.md)。
`SLURRY_FLOW_BALANCE` 使用 `LOGIC27/YK-TLFH/OUT1.CV` 减去
`SY-116/AI1/PV.CV + SY-216/AI1/PV.CV` 的 60 秒尾随平均偏差；偏差低于 -15 或
高于 +15 且连续达到 300 秒产生事件，每次 2 分。三个信号缺一时不把缺失值当作 0。

### 酯化液位变化速率考核

规则目录见
[`level_rate_compliance`](src/dcs_performance/rules/level_rate_compliance/README.md)。
`LICA-012019/PID1/PV.CV` 先作 60 秒尾随平均，再计算 2 小时变化速率；低于
-0.14 或高于 +0.14 液位/小时并连续达到 7200 秒产生下降/上升事件，每次 2 分。
该规则只考核变化速率，绝对液位由 `analog_limit_exceedance` 另行考核。

### 泵组流量考核

规则目录见
[`pump_flow_compliance`](src/dcs_performance/rules/pump_flow_compliance/README.md)。
当前配置为启用，包含 `117P01`、`115P05`、`115P03`、`217P01`、`215P05` 和
`215P03` 六个泵组。规则根据 A/B
泵状态重建正常运行或切泵状态，并检测动态最低流量和切泵超时；切泵超时严格使用
`duration > max_switch_duration_seconds`。默认评分为低流量事件 1 分、切泵超时事件
2 分。

## 完整执行链路

```text
排班配置
  -> Cyclic12HourShiftCalendar
  -> CalendarShiftResolver
  -> Shift
  -> RuleLoader.load_enabled()
  -> build_assessment_window(shift, rule_config)
  -> 点位 assessment_window 覆盖（如有）
  -> 按 effective window 分组
  -> Rule.evaluate(window.start_time, window.end_time, point_ids=group_point_ids)
  -> EvaluatedAssessmentEvent
  -> AssessmentScorer
  -> AssignedAssessmentEvent
  -> ShiftAssessmentSummary
```

`AssessmentEngine.run(shift)` 继续返回 `list[AssessmentEvent]`；需要评分和汇总时使用
`run_detailed(shift)`，其中每个结果保留产生它的规则、正式班次、责任窗口和配置。
`AssessmentScorer` 使用 `EvaluatedAssessmentEvent.shift` 归属班组，不根据事件时间
重新解析正式班次，因此 `07:55` 这类提前责任窗口事件不会被错误归到上一班。

## 本阶段边界

本阶段不包含 PostgreSQL、事件持久化、Event checkpoint 增量 API、定时调度、Web
API、实时推送、调班/换班/加班或人工班次覆盖。`EventCursor` 只是内部预留模型，
当前客户端不提供增量同步接口。真实服务只需用于最终人工 probe；pytest 使用内存
`FakeDataClient`，不访问网络。

## 开发与测试

项目使用 `src` 布局，运行时绘图依赖由 `matplotlib` 提供。安装本地包并运行测试：

```bash
pip install -e .
pytest
```

也可以直接在项目根目录运行 `pytest`；`pyproject.toml` 已为测试配置 `src` 路径。

CLI 当前提供规则目录检查功能：

```bash
dcs-performance --list-rules
```

也可以使用 `--rules-dir <目录>` 指定另一组规则目录。CLI 当前不负责执行班次考核、
评分或写入结果。

## DCS 数据访问层

数据访问实现位于 `src/dcs_performance/data/`，规则层只依赖
`DcsDataClient`。默认通过局域网访问 dcs-service：

```text
http://192.168.1.10:8088
```

也可以通过 `DCS_SERVICE_BASE_URL` 或构造参数覆盖 Base URL：

```python
from dcs_performance.data import DcsServiceClient

client = DcsServiceClient(
    base_url="http://192.168.1.10:8088",
)
history = client.get_history("TAG1", start_time, end_time)
events = client.get_events(start_time, end_time)
```

数据层负责 HTTP GET、URL 编码、CSV Schema、源时区、错误分类、有限重试和
完整范围流式 CSV。对 `get_history()` 或 `get_events()` 传入的一个业务范围，各自只
发送一个请求，不传分页参数；`get_histories()` 按 TAG 发起请求并遵守服务端并发上限。
流中断时丢弃 partial data 并整段重试。请求时间是 DCS 源本地的 naive `datetime`，
不是 UTC；规则不需要接触 HTTP、CSV、Header 或 Event checkpoint。协议原文仍以
[`docs/API-ACCESS.zh-CN.md`](docs/API-ACCESS.zh-CN.md) 为准，客户端使用说明见
[`docs/data-client.md`](docs/data-client.md)。

`timeout_seconds` 控制单次网络连接/读取等待；`total_timeout_seconds` 默认为 `None`，
显式设置时只是客户端操作的软总时间预算，用于排队、请求建立、重试、退避和完成检查，
不保证精确打断正在进行的底层阻塞读取。

需要窗口前状态的规则通过 `history_context` 查询前置样本。当前反向 lookback 搜索
使用 30 分钟、2 小时、12 小时和 48 小时业务 horizon，按这些业务范围直接查询，
不因为 dcs-service 的技术限制固定切成 24 小时；这属于上下文搜索策略，不改变单次
`DcsServiceClient.get_history()` 调用一个范围一个 HTTP 请求的接口语义。

本阶段不要求本地运行 dcs-service；默认 pytest 全部使用内存 FakeTransport。人工连接
真实服务时使用 [`experiments/dcs_service/probe.py`](experiments/dcs_service/probe.py)：

```bash
python experiments/dcs_service/probe.py \
  --base-url http://192.168.1.10:8088 \
  --tag "<REAL_TAG>" \
  --from 2026-08-31T00:00:00 \
  --to 2026-08-31T02:00:00
```

probe 会依次检查 Health、Info、TAG、History 和 Event，并打印流窗口、行数及首尾时间。
真实环境验证不由 pytest 代替。局域网地址、接口参数、CSV Schema 和人工下载示例见
[`docs/LOCAL-USAGE.zh-CN.md`](docs/LOCAL-USAGE.zh-CN.md)。

## 目录结构

```text
dcs_performance/
├── README.md
├── pyproject.toml
├── .gitignore
├── src/dcs_performance/
│   ├── data/
│   ├── shifts/
│   ├── core/
│   ├── rules/example_rule/
│   ├── rules/analog_limit_exceedance/
│   ├── rules/analog_trend_stability/
│   ├── rules/persistent_high_alarm/
│   ├── rules/pump_flow_compliance/
│   ├── engine/
│   ├── results/
│   └── cli.py
├── experiments/
├── tests/
└── docs/
```

更多接口说明见：

- [`docs/architecture.md`](docs/architecture.md)
- [`docs/rule-interface.md`](docs/rule-interface.md)
- [`docs/shift-model.md`](docs/shift-model.md)
- [`docs/result-package.md`](docs/result-package.md)
- [`docs/assessment-points.md`](docs/assessment-points.md)

## Result Package 生产运行

```bash
dcs-performance run --at 2026-09-03T13:00:00 --output ./output
```

命令使用现有生产排班配置解析包含 `--at` 的真实班次，执行所有启用规则，并原子
发布 `result.json` 与每个启用 `(rule_id, point_id)` 的 PNG。零事件点同样进入 JSON
并生成证据图。可用 `--service-url`、`--rules-dir` 与显式 `--overwrite` 调整运行。

Each JSON point always contains the effective `assessment_window`, `status`, `data_status`,
`score`, `image`, and `events`. The top-level `summary.data_quality` counts `ok_points`,
`partial_points`, and `no_data_points`; `assessment_complete` is true only when the latter
two counts are zero. `status=normal` together with `data_status=no_data` does not mean the
assessment was completed successfully.

## Result Package 邮件通知

邮件通知只读取已发布的 Result Package，不访问 DCS、Excel 或考核引擎。配置模板见
`notification.config.example.json`；请复制为本机未跟踪的
`notification.config.json`，SMTP 授权码通过 `DCS_SMTP_PASSWORD` 环境变量提供。

```bash
dcs-performance send-email --package ./assessment_reports/<run-id>
dcs-performance send-email --package ./assessment_reports/<run-id> --preview
dcs-performance send-email --package ./assessment_reports/<run-id> --resend
```

邮件只列出 `score > 0` 的考核点，时间使用省略秒数的短格式；扣分汇总后每个考核点
只展示标题和对应 PNG（以 CID 内嵌到 HTML），无扣分班次也会发送通知。报表更新可
追加 `python 报表/report.py update ... --send-email`，邮件只会在 Result Package 校验
和 Excel 原子保存成功后发送。详见 [`docs/notification.md`](docs/notification.md)。
