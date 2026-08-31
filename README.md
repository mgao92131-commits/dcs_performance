# dcs_performance

`dcs_performance` 是一个用于基于 DCS 历史数据和事件数据开展规则化绩效考核的 Python 项目。

当前版本在保留标准三班两倒排班模块的基础上，增加了第一条真实生产考核规则
`persistent_high_alarm`。项目不直接连接 DeltaV/DCS 数据库，而是通过只读
dcs-service V1 HTTP API 获取 DCS 历史数据。

## 核心职责分离

| 模块 | 职责 |
| --- | --- |
| `Rule` | 接收一段时间范围，返回这段时间内需要考核的 `AssessmentEvent` |
| `Rule config` | 保存规则名称、启用状态、考核时间偏移、阈值/参数和评分配置 |
| `Shift` | 表示已经确定的“谁在什么时间上班” |
| `AssessmentWindow` | 将班次时间和规则配置转换为实际传给 `Rule.evaluate()` 的时间范围 |
| `ShiftResolver` / `ShiftCalendar` | 根据配置计算班次并解析时间所属的班组/班次 |
| `Assignment` | 将考核事件归属到班组/班次；有责任窗口的规则使用执行它的正式 `Shift` |
| `Scoring` | 从规则配置读取点位/默认积分，生成 `AssignedAssessmentEvent` |
| `Summary` | 按正式班次、责任窗口和点位汇总事件次数与积分 |
| `Engine` | 加载启用规则、计算考核窗口、执行规则并保留详细执行上下文 |

规则本身不知道 A/B/C 班、白班、夜班或三班两倒。所有规则统一使用以下接口：

```python
events = rule.evaluate(start_time, end_time)
# events: list[AssessmentEvent]
```

`AssessmentEvent` 只描述问题发生的起止时间、消息和规则相关数据，不包含班组或得分。

## 规则目录约定

每个考核点都是独立目录，配置跟随规则存放：

```text
src/dcs_performance/rules/<rule_id>/
    rule.py
    config.json
    README.md
```

第一阶段的 `example_rule` 是一个不访问数据、始终返回空事件列表的接口示例。不存在集中维护所有规则参数的 `rules.json`。

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

## 持续高报考核

规则目录见
[`persistent_high_alarm`](src/dcs_performance/rules/persistent_high_alarm/README.md)。
它从配置中的三个 Historian TAG 读取数字状态：`0` 为正常，`1` 为高报。
一次 `0 -> 1` 连续保持超过 5 分钟才计 1 次；严格 300 秒不计，连续的
多个 `1` 不重复计数，恢复为 `0` 后再次高报才是新事件。

责任窗口由规则配置产生：白班为 `07:50-19:50`，夜班为
`19:50-次日 07:50`。事件按 `alarm_start` 归属，跨正式 `20:00` 不拆分，
窗口外读取的数据只用于状态上下文。评分来自 `scoring.default_score_per_event`
和 `scoring.by_point`，不写死在规则代码中。

配置中的 `REPLACE_WITH_VERIFIED_TAG` 是占位符。部署前必须对三个完整 TAG
分别调用 `DcsServiceClient.check_tag()` 确认；在没有现场确认前，不声称已完成
真实 DCS 数据联调。

## 完整执行链路

```text
排班配置
  -> Cyclic12HourShiftCalendar
  -> CalendarShiftResolver
  -> Shift
  -> RuleLoader.load_enabled()
  -> build_assessment_window(shift, rule_config)
  -> Rule.evaluate(window.start_time, window.end_time)
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

本阶段不包含 PostgreSQL、事件持久化、checkpoint、定时调度、Web API、实时推送、
调班/换班/加班或人工班次覆盖。真实服务只需用于最终人工 probe；pytest 使用
内存 `FakeDataClient`，不访问网络。

## 开发与测试

项目使用 `src` 布局。安装开发依赖后可运行：

```bash
pip install -e .
pytest
```

也可以直接在项目根目录运行 `pytest`；`pyproject.toml` 已为测试配置 `src` 路径。

## DCS 数据访问层

第二阶段的数据访问实现位于 `src/dcs_performance/data/`，规则层只依赖
`DcsDataClient`。正式客户端通过构造参数接收 dcs-service V1 的 Base URL：

```python
from dcs_performance.data import DcsServiceClient

client = DcsServiceClient(
    base_url="http://127.0.0.1:18080",
)
history = client.get_history("TAG1", start_time, end_time)
events = client.get_events(start_time, end_time)
```

数据层负责 HTTP GET、URL 编码、CSV Schema、源时区、错误分类、有限重试和
Event 固定范围分页。请求时间是 DCS 源本地的 naive `datetime`，不是 UTC；规则
不需要接触 HTTP、CSV、Header 或 Event cursor。协议原文仍以
[`docs/API-ACCESS.zh-CN.md`](docs/API-ACCESS.zh-CN.md) 为准，客户端使用说明见
[`docs/data-client.md`](docs/data-client.md)。

本阶段不要求本地运行 dcs-service；默认 pytest 全部使用内存 FakeTransport。
人工连接真实服务时使用 [`experiments/dcs_service/probe.py`](experiments/dcs_service/probe.py)。

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
│   ├── rules/persistent_high_alarm/
│   ├── results/
│   ├── engine/
│   └── cli.py
├── experiments/
├── tests/
└── docs/
```

更多接口说明见：

- [`docs/architecture.md`](docs/architecture.md)
- [`docs/rule-interface.md`](docs/rule-interface.md)
- [`docs/shift-model.md`](docs/shift-model.md)
