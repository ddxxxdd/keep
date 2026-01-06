# Provider API Contract: Anomaly Detector Provider

**Date**: 2025-12-29  
**Updated**: 2025-01-27  
**Feature**: 001-anomaly-detector-provider  
**Provider Type**: `anomaly_detector`

## Overview

本文档定义 Anomaly Detector Provider 的 `_query()` 方法接口契约，包括输入参数、返回格式和错误处理。

## Method: `_query()`

### Signature

```python
def _query(
    self,
    metric: str,
    data_source: Optional[str] = None,
    time_range: Optional[str] = None,
    **kwargs: Any
) -> Dict[str, Any]:
```

### Parameters

| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| `metric` | `str` | Yes | - | 查询表达式：<br>- Prometheus: PromQL（如 `"keep_http_server_duration_seconds_bucket"` 或完整 PromQL）<br>- Tempo: TraceQL（如 `'{ .service_name = "frontend" } \| count()'`）<br>- Loki: LogQL（如 `'sum(rate({job="varlogs"}[5m])) by (level)'`） |
| `data_source` | `str` | No | `"prometheus"` | 数据源类型，必须是 `"prometheus"`, `"tempo"`, `"loki"` 之一 |
| `time_range` | `str` | No | Provider 配置值 | 人类可读时间范围，格式：`\d+[smhd]`（如 `"30m"`, `"1h"`, `"2d"`） |

### Return Type

`Dict[str, Any]` - 异常检测结果对象，结构如下：

#### Success Response (status: "success" | "normal")

```json
{
  "metric": "string",
  "query": "string",
  "data_source": "prometheus" | "tempo" | "loki",
  "status": "success" | "normal",
  "total_points": 120,
  "anomaly_count": 3,
  "mean": 0.045,
  "std": 0.012,
  "algorithm": "rate_change" | "zscore" | "isolation_forest" | "combined",
  "anomalies": [
    {
      "index": 85,
      "value": 0.089,
      "score": 1.87,
      "method": "rate_change" | "zscore" | "isolation_forest",
      "details": {
        "baseline_value": 0.042,
        "rate_change_threshold": 0.5,
        "startup_exclusion": 36
      }
    }
  ]
}
```

#### No Data Response (status: "no_data")

```json
{
  "metric": "string",
  "query": "string",
  "data_source": "prometheus" | "tempo" | "loki",
  "status": "no_data",
  "anomalies": []
}
```

#### Insufficient Data Response (status: "insufficient_data")

```json
{
  "metric": "string",
  "query": "string",
  "data_source": "prometheus" | "tempo" | "loki",
  "status": "insufficient_data",
  "data_points": 5,
  "min_data_points": 20,
  "anomalies": []
}
```

### Error Responses

#### Configuration Error

当 `data_source` 指定的数据源未启用时：

```python
raise ValueError(f"数据源 '{data_source}' 未在 Provider 配置中启用")
```

#### Data Source Connection Error

当数据源连接失败时：

```python
raise RuntimeError(f"无法连接到 {data_source}: {error_message}")
```

#### Invalid Query Error

当查询表达式无效时：

```python
raise ValueError(f"无效的查询表达式: {metric}")
```

### Examples

#### Example 1: Prometheus Query (Default)

**Input**:
```python
_query(
    metric="keep_http_server_duration_seconds_bucket",
    time_range="1h"
)
```

**Output**:
```json
{
  "metric": "keep_http_server_duration_seconds_bucket",
  "query": "rate(keep_http_server_duration_seconds_bucket[3m])",
  "data_source": "prometheus",
  "status": "success",
  "total_points": 120,
  "anomaly_count": 2,
  "mean": 0.045,
  "std": 0.012,
  "algorithm": "rate_change",
  "anomalies": [...]
}
```

#### Example 2: Tempo Query

**Input**:
```python
_query(
    data_source="tempo",
    metric='{ .service_name = "frontend" } | count()',
    time_range="30m"
)
```

**Output**:
```json
{
  "metric": "{ .service_name = \"frontend\" } | count()",
  "query": "{ .service_name = \"frontend\" } | count()",
  "data_source": "tempo",
  "status": "normal",
  "total_points": 60,
  "anomaly_count": 0,
  "mean": 125.5,
  "std": 12.3,
  "algorithm": "rate_change",
  "anomalies": []
}
```

#### Example 3: Loki Query

**Input**:
```python
_query(
    data_source="loki",
    metric='sum(rate({job="varlogs"}[5m])) by (level)',
    time_range="1h"
)
```

**Output**:
```json
{
  "metric": "sum(rate({job=\"varlogs\"}[5m])) by (level)",
  "query": "sum(rate({job=\"varlogs\"}[5m])) by (level)",
  "data_source": "loki",
  "status": "success",
  "total_points": 72,
  "anomaly_count": 1,
  "mean": 45.2,
  "std": 8.7,
  "algorithm": "zscore",
  "anomalies": [...]
}
```

### Validation Rules

1. **`metric` 参数**:
   - 不能为空字符串
   - 对于 Prometheus，如果是不完整的指标名（如 `"metric_name"`），Provider 会自动判断是否为计数型指标并包装 `rate()`

2. **`data_source` 参数**:
   - 必须是 `"prometheus"`, `"tempo"`, `"loki"` 之一
   - 如果指定了 `data_source` 但 Provider 配置中对应数据源未启用（`*_enabled=False`），应抛出 `ValueError`

3. **`time_range` 参数**:
   - 格式：`\d+[smhd]`（数字 + 单位：秒/分钟/小时/天）
   - 解析失败时回退到 Provider 配置的 `query_range_seconds`
   - 示例有效值：`"30s"`, `"5m"`, `"1h"`, `"2d"`

### Status Values

| Status | Meaning | When Returned |
|--------|---------|---------------|
| `"success"` | 检测完成，发现异常 | `anomaly_count > 0` |
| `"normal"` | 检测完成，未发现异常 | `anomaly_count == 0` 且数据充足 |
| `"no_data"` | 查询无数据 | 数据源返回空结果 |
| `"insufficient_data"` | 数据点不足 | `data_points < min_data_points` |

### Notes

- Provider 会自动处理计数型 Prometheus 指标的 `rate()` 包装
- 对于 Tempo 和 Loki，`metric` 参数直接作为 TraceQL/LogQL 查询执行，不做自动转换
- 所有时间戳和数值使用 `float` 类型
- 异常点列表按时间索引（`index`）排序

