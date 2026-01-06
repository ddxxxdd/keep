# 日志记录接口规范

**Date**: 2025-01-27  
**Feature**: 002-anomaly-detection-logs

## Overview

本文档定义 Anomaly Detector Provider 日志记录的接口规范，包括日志格式、字段定义和日志级别策略。

## 日志格式

### JSON 格式（生产环境）

当 `LOG_FORMAT=open_telemetry` 时，日志使用 JSON 格式输出：

```json
{
  "timestamp": "2025-01-27T10:30:45.123Z",
  "severity": "INFO",
  "message": "异常检测完成: 状态=normal, 总数据点=120, 异常数量=0",
  "name": "keep.providers.anomaly_detector_provider.anomaly_detector_provider",
  "filename": "anomaly_detector_provider.py",
  "module": "anomaly_detector_provider",
  "worker_type": "api",
  "threadName": "MainThread",
  "process": 12345,
  "otelTraceID": "abc123...",
  "otelSpanID": "def456...",
  "provider_type": "anomaly_detector",
  "workflow_id": "workflow-123",
  "workflow_execution_id": "execution-456",
  "step_id": "step-789",
  "tenant_id": "tenant-001",
  "metric": "keep_http_server_duration_seconds_bucket",
  "data_source": "prometheus",
  "time_range": "1h",
  "algorithm": "zscore",
  "status": "normal",
  "total_points": 120,
  "anomaly_count": 0,
  "mean": 0.045,
  "std": 0.012
}
```

### 开发终端格式（本地开发）

当 `LOG_FORMAT=dev_terminal` 时，日志使用开发终端格式输出：

```
2025-01-27 10:30:45,123 - MainThread INFO - 异常检测完成: 状态=normal, 总数据点=120, 异常数量=0 [provider_type: anomaly_detector] [workflow_id: workflow-123] [workflow_execution_id: execution-456] [step_id: step-789] [tenant_id: tenant-001] [metric: keep_http_server_duration_seconds_bucket] [data_source: prometheus] [time_range: 1h] [algorithm: zscore] [status: normal] [total_points: 120] [anomaly_count: 0] [mean: 0.045] [std: 0.012]
```

## 日志字段规范

### 必需字段

所有日志记录都必须包含以下字段：

| 字段名 | 类型 | 说明 | 示例 |
|--------|------|------|------|
| `timestamp` | `str` | 日志时间戳（ISO 8601 格式） | `"2025-01-27T10:30:45.123Z"` |
| `severity` / `level` | `str` | 日志级别 | `"INFO"`, `"DEBUG"`, `"WARNING"`, `"ERROR"` |
| `message` | `str` | 日志消息（人类可读） | `"异常检测完成: 状态=normal"` |
| `provider_type` | `str` | Provider 类型 | `"anomaly_detector"` |
| `tenant_id` | `str` | 租户 ID | `"tenant-001"` |

### 上下文字段（如果可用）

以下字段在工作流执行上下文中可用时应该包含：

| 字段名 | 类型 | 说明 | 示例 |
|--------|------|------|------|
| `workflow_id` | `str` | 工作流 ID | `"workflow-123"` |
| `workflow_execution_id` | `str` | 工作流执行 ID | `"execution-456"` |
| `step_id` | `str` | 步骤 ID | `"step-789"` |

### 检测参数字段

以下字段在检测开始时记录：

| 字段名 | 类型 | 说明 | 示例 |
|--------|------|------|------|
| `metric` | `str` | 查询的指标/查询表达式 | `"keep_http_server_duration_seconds_bucket"` |
| `data_source` | `str` | 数据源类型 | `"prometheus"`, `"tempo"`, `"loki"` |
| `time_range` | `str` | 查询时间范围（可选） | `"30m"`, `"1h"`, `"2d"` |
| `algorithm` | `str` | 检测算法类型 | `"zscore"`, `"isolation_forest"`, `"both"`, `"rate_change"` |

### 执行结果字段

以下字段在检测完成时记录：

| 字段名 | 类型 | 说明 | 示例 |
|--------|------|------|------|
| `status` | `str` | 检测状态 | `"success"`, `"normal"`, `"no_data"`, `"insufficient_data"` |
| `total_points` | `int` | 总数据点数量 | `120` |
| `anomaly_count` | `int` | 检测到的异常点数量 | `3` |
| `mean` | `float` | 数据点的平均值 | `0.045` |
| `std` | `float` | 数据点的标准差 | `0.012` |
| `query` | `str` | 实际执行的查询语句（可选） | `"rate(keep_http_server_duration_seconds_bucket[3m])"` |

### 调试字段（DEBUG 级别）

以下字段仅在 DEBUG 级别日志中记录：

| 字段名 | 类型 | 说明 | 示例 |
|--------|------|------|------|
| `min_value` | `float` | 数据点的最小值 | `0.012` |
| `max_value` | `float` | 数据点的最大值 | `0.089` |
| `range_seconds` | `int` | 查询时间范围（秒） | `3600` |
| `start_time` | `float` | 查询开始时间（Unix 时间戳） | `1706352645.123` |
| `end_time` | `float` | 查询结束时间（Unix 时间戳） | `1706356245.123` |
| `anomalies_summary` | `List[Dict]` | 异常点摘要（前 10 个） | `[{"index": 85, "value": 0.089, "score": 1.87}]` |

### 错误字段（ERROR 级别）

以下字段仅在发生错误时记录：

| 字段名 | 类型 | 说明 | 示例 |
|--------|------|------|------|
| `error_message` | `str` | 错误消息 | `"Prometheus 连接失败"` |
| `error_traceback` | `str` | 错误堆栈跟踪（可选） | `"Traceback (most recent call last):..."` |

### 数据不足字段（WARNING 级别）

以下字段仅在数据不足时记录：

| 字段名 | 类型 | 说明 | 示例 |
|--------|------|------|------|
| `data_points` | `int` | 当前可用数据点数量 | `5` |
| `min_data_points` | `int` | 所需最小数据点数量 | `10` |

## 日志级别策略

### INFO 级别

用于记录正常执行流程和结果：

- 检测开始
- 查询执行
- 数据获取成功
- 检测完成（无论结果如何）

### DEBUG 级别

用于记录详细的执行细节：

- 算法执行细节（基线计算、阈值比较等）
- 数据统计信息（最小值、最大值等）
- 查询时间范围详情

### WARNING 级别

用于记录非致命性问题：

- 数据不足
- 查询返回空结果
- 配置问题

### ERROR 级别

用于记录严重错误：

- 数据源连接失败
- 查询超时
- 数据解析错误
- 其他未预期的异常

## 日志记录点

### 1. 检测开始

**级别**: INFO  
**消息**: `"开始执行异常检测: metric={metric}, data_source={data_source}, time_range={time_range}"`  
**字段**: `metric`, `data_source`, `time_range`, `algorithm`

### 2. 查询执行

**级别**: INFO  
**消息**: `"执行 {data_source} 查询: {query}"`  
**字段**: `query`, `data_source`, `range_seconds` (DEBUG)

### 3. 数据获取

**级别**: INFO (成功) / WARNING (失败)  
**消息**: 
- 成功: `"从 {data_source} 获取到 {data_points} 个数据点"`
- 失败: `"{data_source} 查询返回空结果: {query}"` 或 `"数据点不足: 当前 {data_points} 个，需要至少 {min_data_points} 个"`  
**字段**: `data_points`, `min_data_points` (如果数据不足)

### 4. 检测算法执行（可选）

**级别**: DEBUG  
**消息**: `"数据统计: 最小值={min_value}, 最大值={max_value}, 均值={mean}, 标准差={std}"`  
**字段**: `min_value`, `max_value`, `mean`, `std`

### 5. 检测完成

**级别**: INFO (正常/有异常) / WARNING (无数据/数据不足) / ERROR (错误)  
**消息**: 
- 正常: `"异常检测完成: 状态=normal, 总数据点={total_points}, 异常数量=0"`
- 有异常: `"异常检测完成: 状态=success, 总数据点={total_points}, 异常数量={anomaly_count}"`
- 无数据: `"异常检测完成: 状态=no_data"`
- 数据不足: `"异常检测完成: 状态=insufficient_data, 数据点={data_points}, 最小要求={min_data_points}"`  
**字段**: `status`, `total_points`, `anomaly_count`, `mean`, `std`, `algorithm`

### 6. 异常详情（可选）

**级别**: WARNING (当有异常时)  
**消息**: `"检测到 {anomaly_count} 个异常点"`  
**字段**: `anomaly_count`, `anomalies_summary` (前 10 个异常点)

### 7. 错误记录

**级别**: ERROR  
**消息**: `"异常检测执行失败: {error_message}"`  
**字段**: `error_message`, `error_traceback`

## 实现示例

```python
import logging
import threading

logger = logging.getLogger(__name__)

# 获取上下文信息
workflow_id = self.context_manager.workflow_id
workflow_execution_id = self.context_manager.workflow_execution_id
tenant_id = self.context_manager.tenant_id
step_id = getattr(threading.current_thread(), "step_id", None)

# 记录检测开始
logger.info(
    f"开始执行异常检测: metric={metric}, data_source={data_source}, time_range={time_range or 'default'}",
    extra={
        "provider_type": "anomaly_detector",
        "workflow_id": workflow_id,
        "workflow_execution_id": workflow_execution_id,
        "step_id": step_id,
        "tenant_id": tenant_id,
        "metric": metric,
        "data_source": data_source,
        "time_range": time_range,
        "algorithm": self._config.algorithm,
    }
)

# 记录检测完成
logger.info(
    f"异常检测完成: 状态={status}, 总数据点={total_points}, 异常数量={anomaly_count}",
    extra={
        "provider_type": "anomaly_detector",
        "workflow_id": workflow_id,
        "workflow_execution_id": workflow_execution_id,
        "step_id": step_id,
        "tenant_id": tenant_id,
        "metric": metric,
        "data_source": data_source,
        "status": status,
        "total_points": total_points,
        "anomaly_count": anomaly_count,
        "mean": mean,
        "std": std,
        "algorithm": algorithm,
    }
)
```

## 查询示例

### LogQL 查询示例（Loki）

```logql
# 查询特定工作流的所有异常检测日志
{provider_type="anomaly_detector", workflow_id="workflow-123"}

# 查询有异常的检测记录
{provider_type="anomaly_detector"} | json | status="success" | anomaly_count > 0

# 查询特定指标的检测记录
{provider_type="anomaly_detector"} | json | metric=~"keep_http_server.*"

# 查询数据不足的记录
{provider_type="anomaly_detector"} | json | status="insufficient_data"
```

## Notes

- 所有字段名使用小写字母和下划线（snake_case）
- 时间戳使用 ISO 8601 格式（JSON）或 Unix 时间戳（数值字段）
- 日志消息应该清晰、简洁，包含关键信息
- 结构化字段通过 `extra` 参数传递，由日志格式化器自动处理
- 日志记录不应影响异常检测的执行性能

