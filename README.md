# dcs_performance

`dcs_performance` 是一个用于基于 DCS 历史数据和事件数据开展规则化绩效考核的 Python 项目。

当前提交只完成第一阶段的项目骨架和核心接口，不包含真实生产考核逻辑，不连接 DCS 或数据库，也不实现正式的三班两倒排班算法。

## 核心职责分离

| 模块 | 职责 |
| --- | --- |
| `Rule` | 接收一段时间范围，返回这段时间内需要考核的 `AssessmentEvent` |
| `Rule config` | 保存规则名称、启用状态、考核时间偏移、阈值/参数和评分配置 |
| `Shift` | 表示已经确定的“谁在什么时间上班” |
| `AssessmentWindow` | 将班次时间和规则配置转换为实际传给 `Rule.evaluate()` 的时间范围 |
| `ShiftResolver` / `ShiftCalendar` | 提供班次解析和未来排班日历的接口；第一阶段不实现正式排班算法 |
| `Assignment` | 将考核事件归属到班组/班次；第一阶段只提供基础数据结构和单班实现 |
| `Engine` | 加载启用规则、计算考核窗口、执行规则并收集事件 |

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

## 基础执行链路

```text
Shift
  -> RuleLoader.load_enabled()
  -> build_assessment_window(shift, rule_config)
  -> Rule.evaluate(window.start_time, window.end_time)
  -> list[AssessmentEvent]
```

评分结果模型 `AssignedAssessmentEvent` 和事件归属接口已建立，但当前 Engine 不进行班组汇总、评分或结果存储。

## 开发与测试

项目使用 `src` 布局。安装开发依赖后可运行：

```bash
pip install -e .
pytest
```

也可以直接在项目根目录运行 `pytest`；`pyproject.toml` 已为测试配置 `src` 路径。

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
