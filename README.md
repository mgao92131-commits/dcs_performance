# dcs_performance

`dcs_performance` 是一个用于基于 DCS 历史数据和事件数据开展规则化绩效考核的 Python 项目。

当前仓库包含一套可配置的班次级考核执行链，以及四个规则目录：
`persistent_high_alarm`、`analog_limit_exceedance`、`analog_trend_stability` 和
`pump_flow_compliance`，另有一个不访问数据的 `example_rule` 接口样例。规则不直接
连接 DeltaV/DCS 数据库，而是通过只读 dcs-service V1 HTTP API 获取 DCS 数据。

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

| 规则 ID | 名称 | 默认状态 | 当前用途 |
| --- | --- | --- | --- |
| `analog_limit_exceedance` | 连续量上下限超限考核 | 启用 | 连续量上下限超限 |
| `analog_trend_stability` | 连续量趋势稳定性考核 | 启用 | 趋势偏差和趋势漂移 |
| `example_rule` | 示例考核规则 | 启用 | 空事件接口样例 |
| `persistent_high_alarm` | 持续高报考核 | 启用 | 数字量持续高报 |
| `pump_flow_compliance` | 泵组流量考核 | 停用 | 泵组低流量和切泵超时 |

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
| `TI-013008` | `TI-013008/AI1/PV.CV` | 80 | 120 | 300 秒 |
| `LICA-012019` | `LICA-012019/PID1/PV.CV` | 45 | 55 | 600 秒 |

只有持续时间严格大于各方向的 `min_duration_seconds` 才生成事件；短暂恢复可按
`merge_gap_seconds` 合并。事件记录超限方向、持续时间和极值，评分由
`scoring.by_point_event_type` 配置，当前两个点位的低限/高限分值分别为 1/1 和 2/2。

### 连续量趋势稳定性考核

规则目录见
[`analog_trend_stability`](src/dcs_performance/rules/analog_trend_stability/README.md)。
当前配置针对 `LICA-012019/PID1/PV.CV`，使用 `rolling_mean` 趋势，同时支持：

- 稳定性偏差：按 warning/high 阈值和持续时间生成事件；
- 趋势漂移：按多个时间窗口比较趋势变化，合并同点同方向的结果；
- 数据质量保护：非法值、Historian/CR hole、手工删除/插入和过大时间缺口会切断
  有效数据段。

评分使用事件的 `point_id` 和 `score_key`，不在规则代码中写死。

### 泵组流量考核

规则目录见
[`pump_flow_compliance`](src/dcs_performance/rules/pump_flow_compliance/README.md)。
当前配置为停用，包含 `117P01`、`115P05` 和 `115P03` 三个泵组。规则根据 A/B
泵状态重建正常运行或切泵状态，并检测动态最低流量和切泵超时；切泵超时严格使用
`duration > max_switch_duration_seconds`。默认配置尚未设置正式评分，启用前应先
补充 `scoring`。

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

本阶段不包含 PostgreSQL、事件持久化、Event checkpoint 增量 API、定时调度、Web
API、实时推送、调班/换班/加班或人工班次覆盖。`EventCursor` 只是内部预留模型，
当前客户端不提供增量同步接口。真实服务只需用于最终人工 probe；pytest 使用内存
`FakeDataClient`，不访问网络。

## 开发与测试

项目使用 `src` 布局，当前没有额外的运行时依赖。安装本地包并运行测试：

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
