# dcs_performance 架构

## 目标

当前阶段建立从生产排班到班次汇总的执行链路：

```text
排班配置
  -> ShiftCalendar
  -> ShiftResolver
  -> Shift
  -> AssessmentWindow
  -> Rule
  -> EvaluatedAssessmentEvent
  -> Scoring
  -> AssignedAssessmentEvent
  -> ShiftAssessmentSummary
```

规则仍然可以单独使用原有接口：

```text
Shift
  -> RuleLoader.load_enabled()
  -> AssessmentWindow
  -> Rule.evaluate(start_time, end_time)
  -> list[AssessmentEvent]
```

生产规则通过构造时注入的 `DcsDataClient` 读取历史数据；Engine 不知道具体规则
类型，Scoring 和 Summary 在规则返回事件后工作。

## 模块边界

### `data/`

`DcsDataClient` 是 DCS 适配器协议。规则可以在构造时持有 `data_client`，但 DCS
客户端不作为 `evaluate()` 参数传入。历史点和原始事件的边界模型位于
`data/models.py`。`history_context.py` 只通过既有 `get_history()` 请求补充窗口前
最近状态，不增加 dcs-service API；DCS 异常向上传递，不被转为空事件。

### `core/`

- `event.py`：统一的 `AssessmentEvent`。
- `rule.py`：所有规则必须满足的 `AssessmentRule` 协议。
- `window.py`：根据班次和规则本地配置生成实际考核时间段。
- `result.py`：`AssignedAssessmentEvent`，包括正式班次、责任窗口、事件和积分。
- `evaluation.py`：`EvaluatedAssessmentEvent`，保留规则/班次/窗口/配置上下文。

### `shifts/`

`Shift` 只表达已经解析好的正式班次。`schedule.py` 和
`ThreeTeamTwoShiftCalendar` 保留原有六天白白夜夜休休实现；
`cyclic_schedule.py`/`cyclic_calendar.py` 独立实现生产乙丙甲每 12 小时轮换。
`CalendarShiftResolver` 继续只调用 Calendar，不拥有排班算法；
`StaticShiftResolver` 仍供测试/实验使用。

`Shift` 是正式排班，`AssessmentWindow` 是某条规则的责任范围。二者不能混用：
持续高报规则用正式 `08:00/20:00` Shift，通过 `-10/+10` 分钟配置得到
`07:50/19:50` 责任窗口。

### `rules/`

每个规则目录自带 `rule.py`、`config.json` 和业务说明 `README.md`。规则配置不
集中到项目级的大型 JSON 文件。`persistent_high_alarm` 的 detector 只处理
`HistorySample -> AlarmOccurrence` 状态机，Rule 负责数据读取和责任窗口过滤，
不负责排班、HTTP、CSV 或评分。

### `engine/`

- `loader.py`：按目录发现 `rule.py` 和相邻 `config.json`，构造 `Rule` 实例。
- `runner.py`：对一条规则构造窗口；`run_detailed()` 包装为
  `EvaluatedAssessmentEvent`。
- `engine.py`：遍历启用规则，提供兼容的 `run()` 和保留上下文的
  `run_detailed()`，没有任何规则 ID 分支。

### `results/`

- `scorer.py`：只从 `EvaluatedAssessmentEvent.config["scoring"]` 读取积分，
  使用其中保存的正式 `Shift` 和 `AssessmentWindow`。
- `summary.py`：按班次/责任窗口汇总事件总数、总分和配置点位明细；没有事件的
  配置点位也可以显示为 0 次、0 分。

## 数据流与责任

规则返回的 `AssessmentEvent` 不包含班组或积分。Runner 将执行上下文保存在
`EvaluatedAssessmentEvent`；Scorer 使用其中的 `shift`，不根据事件开始时间重新
调用正式 `ShiftResolver`。因此责任窗口提前 10 分钟时，窗口内但正式 Shift
之前的事件仍归执行该窗口的班组。最终 Summary 再按 `AssignedAssessmentEvent`
汇总。

规则查询窗口可以为了确认持续时间向责任窗口结束后扩展
`threshold + 1 秒`，但最终事件只保留 `window.start <= alarm_start < window.end`。
这就是跨班事件不拆分、下一班不重复统计的边界。

## 当前明确不做的事情

- PostgreSQL、DCS 数据库或外部服务器连接
- 加班、调班、替班、临时班次、节假日特殊班次和人工覆盖排班
- 结果持久化、checkpoint、定时调度和实时推送
- Web/API、网页界面和人工调班/加班覆盖
- Python entry points 插件框架
