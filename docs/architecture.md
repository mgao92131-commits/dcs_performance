# 第一阶段架构

## 目标

第一阶段建立从一个已确定班次到规则事件列表的最小执行链路：

```text
Shift
  -> RuleLoader.load_enabled()
  -> AssessmentWindow
  -> Rule.evaluate(start_time, end_time)
  -> list[AssessmentEvent]
```

这条链路不连接数据库、不访问网络、不做班组汇总、不计算最终得分。

## 模块边界

### `data/`

`DcsDataClient` 是未来 DCS 适配器的协议。规则可以在构造时持有
`data_client`，但 DCS 客户端不作为 `evaluate()` 参数传入。历史点和原始事件的
边界模型位于 `data/models.py`。

### `core/`

- `event.py`：统一的 `AssessmentEvent`。
- `rule.py`：所有规则必须满足的 `AssessmentRule` 协议。
- `window.py`：根据班次和规则本地配置生成实际考核时间段。
- `result.py`：为后续归属和评分阶段预留的 `AssignedAssessmentEvent`。

### `shifts/`

`Shift` 只表达已经解析好的班次。`schedule.py` 负责读取并严格验证固定标准三班
两倒的 JSON 配置，`ThreeTeamTwoShiftCalendar` 负责按配置周期生成班次，
`CalendarShiftResolver` 通过 Calendar 解析时间所属班次；`StaticShiftResolver`
仍供测试/实验使用。`assignment.py` 提供事件归属协议和单班事件的基础实现。
跨班事件拆分策略被明确延后。

### `rules/`

每个规则目录自带 `rule.py`、`config.json` 和业务说明 `README.md`。规则配置不
集中到项目级的大型 JSON 文件。`example_rule` 只用于验证接口，始终返回空列表。

### `engine/`

- `loader.py`：按目录发现 `rule.py` 和相邻 `config.json`，构造 `Rule` 实例。
- `runner.py`：对一条规则构造窗口并调用 `evaluate()`。
- `engine.py`：遍历启用规则，收集事件。没有任何规则 ID 分支。

## 数据流与责任

规则返回事件时只知道请求的时间范围。班次和团队归属由后续 assignment 阶段
处理；评分由配置和后续结果阶段处理。因此新增考核点通常只需新增规则目录，
不需要修改 Engine。

## 当前明确不做的事情

- PostgreSQL、DCS 数据库或外部服务器连接
- 加班、调班、替班、临时班次、节假日特殊班次和人工覆盖排班
- 真实乙二醇高报或其他生产考核逻辑
- Web/API、数据库结果存储和复杂评分体系
- Python entry points 插件框架
