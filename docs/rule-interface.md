# 规则接口

## 公共协议

```python
class AssessmentRule(Protocol):
    id: str
    name: str

    def evaluate(
        self,
        start_time: datetime,
        end_time: datetime,
    ) -> list[AssessmentEvent]:
        ...
```

规则负责回答：“在这段时间内发生了哪些需要考核的事件？”

规则不得根据 A/B/C 班、白班、夜班或三班两倒做判断，也不得把 `Shift`、`team_id`
或评分对象作为 `evaluate()` 参数。若需要历史值或原始事件，规则可以在初始化
时接收一个 `DcsDataClient`：

```python
rule = Rule(data_client=data_client, config=config)
events = rule.evaluate(start_time, end_time)
```

## 返回模型

```python
@dataclass(frozen=True)
class AssessmentEvent:
    start_time: datetime
    end_time: datetime
    message: str = ""
    data: dict[str, object] = field(default_factory=dict)
```

事件不携带班组、白/夜班或分数。一个问题持续多久，就用一个起止时间明确的
事件表示；同一时间范围内多个问题可以返回多个事件。

## 规则目录

```text
rules/<rule_id>/
├── rule.py
├── config.json
└── README.md
```

`config.json` 至少可以表达：

```json
{
  "id": "example_rule",
  "name": "示例考核规则",
  "enabled": true,
  "assessment_window": {
    "start_offset_minutes": 20,
    "end_offset_minutes": 0
  },
  "scoring": {
    "score_per_event": -2,
    "max_penalty": -10
  },
  "parameters": {}
}
```

Loader 只要求目录包含 `rule.py` 和 `config.json`，并从 `rule.py` 中构造名为
`Rule` 的类。规则代码可以直接测试，也可以由 Engine 统一调用。
