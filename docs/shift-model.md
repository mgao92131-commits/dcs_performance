# 班次模型与边界

## `Shift`

```python
@dataclass(frozen=True)
class Shift:
    team_id: str
    start_time: datetime
    end_time: datetime
    shift_type: str
```

示例：

```python
Shift(
    team_id="A",
    shift_type="day",
    start_time=datetime(2026, 8, 31, 8, 0),
    end_time=datetime(2026, 8, 31, 20, 0),
)
```

`Shift` 是一个已经确定的班次值对象，不负责计算轮换或查找班组。

## `ShiftResolver`

```python
class ShiftResolver(Protocol):
    def resolve(self, timestamp: datetime) -> Shift:
        ...
```

第一阶段提供 `StaticShiftResolver`，只解析构造时传入的一个固定班次，供测试和
实验使用。正式日历不在本阶段实现。

## `ShiftCalendar`

`ShiftCalendar` 预留 `get_shifts(start_time, end_time)` 接口。后续实现可以在此
处理三班两倒、白夜班边界、加班、调班和临时替班，但这些规则不应进入 `Shift`
或普通考核规则。

## 事件归属

`EventAssigner.assign(event)` 返回一个或多个 `AssignedEventSlice`。第一阶段的
`SingleShiftAssigner` 只接受完全落在一个班次内的事件；跨越 20:00 等边界的事件
暂不自动拆分，会显式报告 `NotImplementedError`，避免产生隐含的错误归属。

## 窗口计算

规则本地配置中的：

```json
"assessment_window": {
  "start_offset_minutes": 20,
  "end_offset_minutes": 0
}
```

会将 `08:00 - 20:00` 转换为 `08:20 - 20:00`。班次的真实起止时间属于
`shifts/`，规则只声明自身需要的相对偏移。
