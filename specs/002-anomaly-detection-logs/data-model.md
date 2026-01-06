# Data Model: 异常检测日志记录功能

**Date**: 2025-01-27  
**Feature**: 002-anomaly-detection-logs

## Overview

本文档描述异常检测日志记录功能的数据模型，包括日志条目的结构、字段定义和日志级别策略。

## Entities

### AnomalyDetectionLogEntry

表示单次异常检测执行产生的日志记录，包含检测参数、执行过程、数据统计和检测结果等结构化信息。

**Fields**:

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `timestamp` | `str` | Yes | 日志时间戳（ISO 8601 格式） |
| `level` | `str` | Yes | 日志级别：`DEBUG`, `INFO`, `WARNING`, `ERROR` |
| `message` | `str` | Yes | 日志消息（人类可读） |
| `provider_type` | `str` | Yes | Provider 类型：`"anomaly_detector"` |
| `workflow_id` | `str` | No | 工作流 ID（如果可用） |
| `workflow_execution_id` | `str` | No | 工作流执行 ID（如果可用） |
| `step_id` | `str` | No | 步骤 ID（如果可用） |
| `tenant_id` | `str` | Yes | 租户 ID |
| `metric` | `str` | Yes | 查询的指标/查询表达式（PromQL/TraceQL/LogQL） |
| `data_source` | `str` | Yes | 数据源类型：`"prometheus"`, `"tempo"`, `"loki"` |
| `time_range` | `str` | No | 查询时间范围（如 `"30m"`, `"1h"`） |
| `algorithm` | `str` | Yes | 检测算法类型：`"zscore"`, `"isolation_forest"`, `"both"`, `"rate_change"` |
| `status` | `str` | No | 检测状态：`"success"`（有异常）, `"normal"`（无异常）, `"no_data"`（无数据）, `"insufficient_data"`（数据不足） |
| `total_points` | `int` | No | 总数据点数量 |
| `anomaly_count` | `int` | No | 检测到的异常点数量 |
| `mean` | `float` | No | 数据点的平均值 |
| `std` | `float` | No | 数据点的标准差 |
| `min_value` | `float` | No | 数据点的最小值（DEBUG 级别） |
| `max_value` | `float` | No | 数据点的最大值（DEBUG 级别） |
| `query` | `str` | No | 实际执行的查询语句（可能经过自动包装） |
| `range_seconds` | `int` | No | 查询时间范围（秒） |
| `start_time` | `float` | No | 查询开始时间（Unix 时间戳，DEBUG 级别） |
| `end_time` | `float` | No | 查询结束时间（Unix 时间戳，DEBUG 级别） |
| `data_points` | `int` | No | （仅当 `status="insufficient_data"` 时）当前可用数据点数量 |
| `min_data_points` | `int` | No | （仅当 `status="insufficient_data"` 时）所需最小数据点数量 |
| `error_message` | `str` | No | （仅当发生错误时）错误消息 |
| `error_traceback` | `str` | No | （仅当发生错误时，ERROR 级别）错误堆栈跟踪 |
| `anomalies_summary` | `List[Dict]` | No | 异常点摘要（前 10 个异常点，包含 index, value, score） |

**Validation Rules**:
- `provider_type` 必须为 `"anomaly_detector"`
- `data_source` 必须是 `["prometheus", "tempo", "loki"]` 之一
- `algorithm` 必须是 `["zscore", "isolation_forest", "both", "rate_change"]` 之一
- `status` 必须是 `["success", "normal", "no_data", "insufficient_data"]` 之一（如果存在）
- `level` 必须是 `["DEBUG", "INFO", "WARNING", "ERROR"]` 之一

**Relationships**:
- 一次异常检测执行可能产生多条日志记录（开始、查询、检测、结果等）
- 所有日志记录共享相同的 `workflow_id`, `workflow_execution_id`, `step_id`, `tenant_id` 等上下文信息

---

### LogLevelStrategy

定义不同情况下使用的日志级别策略。

**Levels**:

| 情况 | 日志级别 | 说明 |
|------|---------|------|
| 检测开始 | `INFO` | 记录检测开始，包含查询参数 |
| 查询执行 | `INFO` | 记录查询执行，包含查询表达式和时间范围 |
| 数据获取成功 | `INFO` | 记录从数据源获取的数据点数量 |
| 数据获取失败 | `WARNING` | 记录查询返回空结果或数据不足 |
| 检测算法执行 | `DEBUG` | 记录算法执行细节（基线计算、阈值比较等） |
| 数据统计信息 | `DEBUG` | 记录数据统计信息（最小值、最大值等） |
| 检测完成（正常） | `INFO` | 记录检测完成，状态=正常，异常数量=0 |
| 检测完成（有异常） | `INFO` | 记录检测完成，状态=成功，包含异常数量 |
| 检测完成（无数据） | `WARNING` | 记录检测完成，状态=无数据 |
| 检测完成（数据不足） | `WARNING` | 记录检测完成，状态=数据不足，包含原因 |
| 数据源连接失败 | `ERROR` | 记录连接失败错误，包含错误堆栈 |
| 查询超时 | `ERROR` | 记录查询超时错误 |
| 数据解析错误 | `ERROR` | 记录数据解析错误，包含错误堆栈 |
| 其他异常 | `ERROR` | 记录其他未预期的异常，包含错误堆栈 |

---

## State Transitions

### 日志记录流程

```
[检测开始] → [查询执行] → [数据获取] → [检测算法执行] → [检测完成]
     ↓            ↓            ↓              ↓              ↓
  [INFO]      [INFO]      [INFO/WARNING]  [DEBUG]      [INFO/WARNING/ERROR]
```

**States**:
- **检测开始**: 记录检测开始，包含查询参数
- **查询执行**: 记录查询执行，包含查询表达式
- **数据获取**: 记录从数据源获取的数据，成功为 INFO，失败为 WARNING
- **检测算法执行**: 记录算法执行细节（DEBUG 级别）
- **检测完成**: 记录检测结果，根据结果类型选择日志级别

**Transitions**:
- 每个阶段都会产生一条日志记录
- 如果某个阶段发生错误，会记录 ERROR 级别日志并可能跳过后续阶段
- 所有日志记录都包含相同的上下文信息（workflow_id, step_id, tenant_id 等）

---

## Data Flow

### 日志记录流程

```
异常检测执行
  ↓
获取上下文信息（workflow_id, step_id, tenant_id）
  ↓
记录检测开始日志（INFO）
  ↓
执行数据源查询
  ↓
记录查询执行日志（INFO）
  ↓
获取查询结果
  ↓
记录数据获取日志（INFO/WARNING）
  ↓
执行异常检测算法
  ↓
记录算法执行细节（DEBUG）
  ↓
记录检测完成日志（INFO/WARNING/ERROR）
  ↓
日志系统收集和索引
```

---

## Constraints

1. **上下文信息约束**: 如果 `workflow_id`, `step_id` 等上下文信息不可用，应在日志中明确标记为 `null` 或省略该字段
2. **日志级别约束**: 正常结果必须记录 INFO 级别日志，不能仅记录 WARNING 或 ERROR
3. **数据量约束**: 异常点列表只记录前 10 个异常点的摘要信息，避免日志过大
4. **性能约束**: 日志记录不应显著影响异常检测的执行性能，性能开销不超过 5%
5. **错误处理约束**: 日志写入失败不应导致异常检测功能失败

---

## Notes

- 所有时间戳使用 ISO 8601 格式（JSON 日志）或 Unix 时间戳（结构化字段）
- 数值精度：使用 `float` 类型，足够满足日志记录需求
- 日志格式：支持 JSON 格式（生产环境）和开发终端格式（本地开发）
- 日志聚合：日志应能被 Loki、ELK、Grafana 等日志聚合系统正常收集和索引
- 查询语言：日志中的查询表达式（metric）保持原始格式（PromQL/TraceQL/LogQL）

