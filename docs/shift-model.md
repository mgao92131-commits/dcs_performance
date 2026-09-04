# 三班两倒排班模型

当前版本实现固定的标准三班两倒排班：班组为 A、B、C；白班为 08:00-20:00；夜班
为 20:00-次日 08:00。排班模块只回答“这段时间有哪些班次”和“某个时间属于哪个
班组/班次”，不负责考核、评分或 DCS 数据访问。

## `Shift`

```python
@dataclass(frozen=True)
class Shift:
    team_id: str
    start_time: datetime
    end_time: datetime
    shift_type: str
```

`Shift` 是已经解析完成的班次值对象。它不包含轮换索引、周期、前后班次、加班或
调班信息，也不负责计算轮换。

## JSON 配置

使用 `load_schedule_config(path)` 读取 JSON，得到 `ShiftScheduleConfig`：

```json
{
  "reference_date": "2026-01-01",
  "day_shift": {"start": "08:00", "end": "20:00"},
  "night_shift": {"start": "20:00", "end": "08:00"},
  "teams": {
    "A": ["day", "day", "night", "night", "off", "off"],
    "B": ["night", "night", "off", "off", "day", "day"],
    "C": ["off", "off", "day", "day", "night", "night"]
  }
}
```

`reference_date` 表示每个轮换数组的第 0 项对应的自然日。示例文件中的日期只是
演示值；部署到生产环境前，必须替换为已经确认的实际排班基准日期。

每个班组的数组是一个循环模式，数组长度就是周期长度，核心算法不会假定周期一定
是 6 天。每个周期位置必须恰好有一个 `day`、一个 `night` 和一个 `off`。配置加载
时还会校验班组数量、数组长度、状态名称，以及白/夜班是否为连续的两个 12 小时
班次。当前实现只接受标准的 08:00-20:00 和 20:00-08:00。

日期偏移按以下公式计算，并支持基准日期以前的日期：

```python
days = (target_date - reference_date).days
index = days % cycle_length
```

## `ThreeTeamTwoShiftCalendar`

```python
config = load_schedule_config("src/dcs_performance/shifts/schedule.example.json")
calendar = ThreeTeamTwoShiftCalendar(config)

calendar.get_shifts_for_date(date(2026, 1, 1))
calendar.get_shifts(start_time, end_time)
```

`get_shifts_for_date(day)` 固定返回该自然日开始的两个班次：白班和夜班。处于
`off` 状态的班组不生成 `Shift`。

`get_shifts(start_time, end_time)` 返回与查询范围真正相交的班次，并按
`start_time` 升序排列。查询开始日期的前一天也会被检查，因为前一天的夜班可能
延续到查询开始日凌晨。交集判断统一为：

```python
shift.start_time < end_time and shift.end_time > start_time
```

## 夜班日期语义

夜班归属于夜班开始的自然日。`2026-01-01` 夜班始终表示：

```text
2026-01-01 20:00 <= timestamp < 2026-01-02 08:00
```

因此 `2026-01-02 03:00` 仍然属于 `2026-01-01` 开始的夜班，不属于 `2026-01-02`
夜班。

## 半开区间边界

所有班次均采用 `[start_time, end_time)`：开始时间包含，结束时间不包含。

```text
07:59:59  前一日夜班
08:00:00  当日白班
19:59:59  当日白班
20:00:00  当日夜班
次日07:59:59  当日夜班
次日08:00:00  次日白班
```

所以 08:00 不会同时属于上一夜班，20:00 也不会属于白班。

## `ShiftResolver`

```python
class ShiftResolver(Protocol):
    def resolve(self, timestamp: datetime) -> Shift:
        ...
```

`CalendarShiftResolver` 接收一个 `ShiftCalendar`，通过 Calendar 查询时间戳所属
的唯一班次；轮换计算只存在于 `ThreeTeamTwoShiftCalendar`。原有的
`StaticShiftResolver` 仍保留，用于测试和实验。

```python
resolver = CalendarShiftResolver(calendar)
shift = resolver.resolve(datetime(2026, 9, 10, 14, 0))
```

## 与考核规则的边界

排班模块只提供班次的真实起止时间。规则通过统一接口执行：

```python
rule.evaluate(start_time, end_time, point_ids=None)
```

例如规则可以把白班 `08:00-20:00` 转成自己的 assessment window
`08:20-20:00`，这个窗口逻辑不放入 `shifts/`。

当前版本不支持：

- 加班
- 调班
- 替班
- 临时班次
- 节假日特殊班次
- 人工覆盖排班
- 跨班事件拆分策略
