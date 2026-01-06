# Data Model: Anomaly Detector Provider

**Date**: 2025-12-29  
**Updated**: 2025-01-27  
**Feature**: 001-anomaly-detector-provider

## Overview

本文档描述 Anomaly Detector Provider 扩展后的数据模型，包括配置实体、查询参数、返回结果和内部数据结构。

## Entities

### AnomalyDetectorProviderAuthConfig

Provider 的认证和配置信息，通过 Keep UI 配置并存储在 Secret Manager 中。

**Fields**:

| Field | Type | Required | Default | Description |
|-------|------|----------|---------|-------------|
| `prometheus_url` | `str` | Yes | - | Prometheus 服务地址（如 `http://prometheus:9090`） |
| `prometheus_username` | `str` | No | `""` | Prometheus 基本认证用户名 |
| `prometheus_password` | `str` | No | `""` | Prometheus 基本认证密码（敏感字段） |
| `prometheus_verify_ssl` | `bool` | No | `True` | 是否校验 Prometheus SSL 证书 |
| `tempo_url` | `str` | No | `"http://tempo:3200"` | Tempo 服务地址 |
| `tempo_enabled` | `bool` | No | `False` | 是否启用 Tempo 数据源 |
| `loki_url` | `str` | No | `"http://loki:3100"` | Loki 服务地址 |
| `loki_enabled` | `bool` | No | `False` | 是否启用 Loki 数据源 |
| `algorithm` | `str` | No | `"both"` | 检测算法：`"isolation_forest"`, `"zscore"`, `"both"` |
| `min_data_points` | `int` | No | `20` | 参与检测的最小数据点数量 |
| `history_size` | `int` | No | `1000` | 保留用于分析的历史数据点数量 |
| `query_range_seconds` | `int` | No | `3600` | Prometheus 查询时间范围（秒） |
| `query_step` | `str` | No | `"60s"` | Prometheus 查询步长（如 `"60s"`, `"15s"`） |
| `rate_change_threshold` | `float` | No | `0.5` | 环比变化阈值（例如 0.5 = 相对基线 +50%） |
| `min_anomaly_count_for_alert` | `int` | No | `1` | 触发告警的最小异常数量阈值（只有当 `anomaly_count >= min_anomaly_count_for_alert` 时才发送告警） |

**Validation Rules**:
- `prometheus_url` 必须是有效的 HTTP/HTTPS URL
- `algorithm` 必须是 `["isolation_forest", "zscore", "both"]` 之一
- `min_data_points` 必须 >= 3（Z-Score 最小要求）或 >= 10（Isolation Forest 推荐）
- `query_range_seconds` 必须 > 0
- `query_step` 必须符合 Prometheus 步长格式（如 `"15s"`, `"1m"`, `"5m"`）

**Relationships**:
- 一个 `AnomalyDetectorProviderAuthConfig` 实例对应一个 Provider 安装
- 配置通过 `ProviderConfig.authentication` 字段传递给 `AnomalyDetectorProvider`

---

### Query Parameters (Provider `_query()` method input)

工作流步骤调用 Provider 时传入的查询参数。

**Fields**:

| Field | Type | Required | Default | Description |
|-------|------|----------|---------|-------------|
| `metric` | `str` | Yes | - | 查询表达式：PromQL（Prometheus）、TraceQL（Tempo）或 LogQL（Loki） |
| `data_source` | `str` | No | `"prometheus"` | 数据源类型：`"prometheus"`, `"tempo"`, `"loki"` |
| `time_range` | `str` | No | Provider 配置值 | 人类可读时间范围（如 `"30m"`, `"1h"`, `"2d"`），覆盖 Provider 配置的 `query_range_seconds` |

**Validation Rules**:
- `metric` 不能为空
- `data_source` 必须是 `["prometheus", "tempo", "loki"]` 之一
- 如果 `data_source="tempo"` 但 `tempo_enabled=False`，应返回配置错误
- 如果 `data_source="loki"` 但 `loki_enabled=False`，应返回配置错误
- `time_range` 格式：`\d+[smhd]`（如 `"30m"`, `"2h"`, `"1d"`）

**Example**:
```python
# Prometheus 查询
_query(metric="keep_http_server_duration_seconds_bucket", time_range="1h")

# Tempo 查询
_query(data_source="tempo", metric='{ .service_name = "frontend" } | count()', time_range="30m")

# Loki 查询
_query(data_source="loki", metric='sum(rate({job="varlogs"}[5m])) by (level)', time_range="1h")
```

---

### AnomalyDetectionResult (Provider `_query()` method output)

Provider 查询方法返回的异常检测结果。

**Fields**:

| Field | Type | Description |
|-------|------|-------------|
| `metric` | `str` | 原始查询表达式（PromQL/TraceQL/LogQL） |
| `query` | `str` | 实际执行的查询语句（可能经过自动包装，如 `rate()`） |
| `data_source` | `str` | 数据源类型：`"prometheus"`, `"tempo"`, `"loki"` |
| `status` | `str` | 检测状态：`"success"`（有异常）, `"normal"`（无异常）, `"no_data"`（无数据）, `"insufficient_data"`（数据点不足） |
| `total_points` | `int` | 总数据点数量 |
| `anomaly_count` | `int` | 检测到的异常点数量 |
| `mean` | `float` | 数据点的平均值 |
| `std` | `float` | 数据点的标准差 |
| `algorithm` | `str` | 使用的检测算法（如 `"rate_change"`, `"zscore"`, `"isolation_forest"`） |
| `anomalies` | `List[AnomalyPoint]` | 异常点列表（按时间索引排序） |
| `data_points` | `int` | （仅当 `status="insufficient_data"` 时）当前可用数据点数量 |
| `min_data_points` | `int` | （仅当 `status="insufficient_data"` 时）所需最小数据点数量 |

**AnomalyPoint Structure**:

| Field | Type | Description |
|-------|------|-------------|
| `index` | `int` | 异常点在时间序列中的索引位置 |
| `value` | `float` | 异常点的数值 |
| `score` | `float` | 异常评分（Z-Score 值或 Isolation Forest 决策函数值） |
| `method` | `str` | 检测方法（如 `"rate_change"`, `"zscore"`, `"isolation_forest"`） |
| `details` | `dict` | 检测详情（包含基线值、阈值等上下文信息） |

**Example Response**:
```json
{
  "metric": "keep_http_server_duration_seconds_bucket",
  "query": "rate(keep_http_server_duration_seconds_bucket[3m])",
  "data_source": "prometheus",
  "status": "success",
  "total_points": 120,
  "anomaly_count": 3,
  "mean": 0.045,
  "std": 0.012,
  "algorithm": "rate_change",
  "anomalies": [
    {
      "index": 85,
      "value": 0.089,
      "score": 1.87,
      "method": "rate_change",
      "details": {
        "baseline_value": 0.042,
        "rate_change_threshold": 0.5,
        "startup_exclusion": 36
      }
    }
  ]
}
```

---

### Internal Data Structures

#### TimeSeriesData

数据源客户端返回的原始时间序列数据（内部使用，不暴露给工作流）。

**Fields**:
- `values`: `np.ndarray` - 数值数组（按时间顺序）
- `timestamps`: `List[float]` - 对应的时间戳（Unix 时间戳，秒）
- `labels`: `Dict[str, str]` - 时间序列的标签（如 Prometheus 的 `{job="..."}`）

**Usage**: 所有数据源客户端（Prometheus/Tempo/Loki）统一返回此格式，然后转换为 `np.ndarray` 传入异常检测算法。

---

## State Transitions

### Provider Configuration State

```
[未安装] → [安装中] → [已安装/配置有效] → [已安装/配置无效] → [已卸载]
                ↓                           ↑
            [配置错误] ←──────────────────────┘
```

**States**:
- **未安装**: Provider 尚未在 Keep 中安装
- **安装中**: 用户正在 UI 中配置 Provider
- **已安装/配置有效**: Provider 已安装且配置通过验证（Prometheus 连接成功）
- **已安装/配置无效**: Provider 已安装但配置错误（如 Prometheus URL 不可达）
- **已卸载**: Provider 已从 Keep 中删除

**Transitions**:
- 安装时验证配置：检查 Prometheus URL 可访问性
- 配置变更时重新验证
- 查询时如果配置无效，返回错误信息

---

## Data Flow

### Query Execution Flow

```
工作流步骤
  ↓ (调用 _query(metric="...", data_source="prometheus"))
AnomalyDetectorProvider._query()
  ↓ (根据 data_source 选择客户端)
PrometheusClient / TempoClient / LokiClient
  ↓ (执行查询，返回 TimeSeriesData)
数据转换 (TimeSeriesData → np.ndarray)
  ↓ (传入异常检测算法)
BaseAnomalyDetector.detect()
  ↓ (返回 DetectionResult)
结果格式化 (DetectionResult → AnomalyDetectionResult)
  ↓ (返回给工作流)
工作流步骤接收结果
```

---

## Constraints

1. **数据源启用约束**: 如果查询指定了 `data_source="tempo"` 但 Provider 配置中 `tempo_enabled=False`，应返回配置错误
2. **最小数据点约束**: 如果可用数据点 < `min_data_points`，返回 `status="insufficient_data"` 而不执行检测
3. **时间范围约束**: `time_range` 解析失败时回退到 Provider 配置的 `query_range_seconds`
4. **算法约束**: 如果 `algorithm="isolation_forest"` 但 `scikit-learn` 未安装，应返回依赖错误

---

## Notes

- 所有时间戳使用 Unix 时间戳（秒，float）
- 数值精度：使用 `float` 类型，足够满足异常检测需求
- 异常点列表按 `index`（时间顺序）排序，便于在工作流中按时间处理

