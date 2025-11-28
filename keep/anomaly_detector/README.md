# Anomaly Detector Service

异常检测服务 - 自动从 Prometheus 获取指标，使用机器学习算法检测异常并生成告警。

## 功能特点

- **自动指标发现**: 自动从 Prometheus 发现所有可用指标
- **多种检测算法**: 
  - Isolation Forest (孤立森林) - 无监督机器学习算法
  - Z-Score (3σ) - 基于统计学的标准差检测
  - Combined (组合) - 同时使用多种算法
- **零配置告警**: 无需手动设置阈值，算法自动学习正常模式
- **与 Keep 集成**: 检测到异常自动发送告警到 Keep 平台

## 架构图

```
┌─────────────────────────────────────────────────────────────────────┐
│                    Anomaly Detector Service                          │
│                                                                      │
│   ┌───────────────┐     ┌──────────────────┐     ┌───────────────┐  │
│   │  Prometheus   │────>│  Anomaly Detector │────>│  Keep API     │  │
│   │   (数据源)     │     │   (检测引擎)       │     │  (告警平台)    │  │
│   └───────────────┘     └──────────────────┘     └───────────────┘  │
│                                │                                     │
│                    ┌───────────┴───────────┐                        │
│                    │                       │                        │
│            ┌───────▼───────┐      ┌───────▼───────┐                 │
│            │ IsolationForest│      │    Z-Score    │                 │
│            │   (孤立森林)    │      │   (标准差)    │                 │
│            └───────────────┘      └───────────────┘                 │
└─────────────────────────────────────────────────────────────────────┘
```

## 环境变量配置

### 基本配置

| 环境变量 | 描述 | 默认值 |
|---------|------|-------|
| `ANOMALY_DETECTOR_ENABLED` | 是否启用服务 | `true` |
| `ANOMALY_DETECTOR_INTERVAL` | 检测间隔（秒） | `300` |
| `ANOMALY_DETECTOR_ALGORITHM` | 检测算法 (`isolation_forest`, `zscore`, `both`) | `both` |

### Prometheus 配置

| 环境变量 | 描述 | 默认值 |
|---------|------|-------|
| `PROMETHEUS_URL` | Prometheus 服务器地址 | `http://localhost:9090` |
| `PROMETHEUS_USERNAME` | Prometheus 用户名 | (空) |
| `PROMETHEUS_PASSWORD` | Prometheus 密码 | (空) |
| `PROMETHEUS_VERIFY_SSL` | 是否验证 SSL 证书 | `true` |

### Keep API 配置

| 环境变量 | 描述 | 默认值 |
|---------|------|-------|
| `KEEP_API_URL` | Keep API 地址 | `http://localhost:8080` |
| `KEEP_API_KEY` | Keep API 密钥 | (空) |
| `KEEP_TENANT_ID` | 租户 ID | `singletenant` |

### 算法配置

| 环境变量 | 描述 | 默认值 |
|---------|------|-------|
| `ANOMALY_DETECTOR_CONTAMINATION` | Isolation Forest 污染率（预期异常比例） | `0.05` |
| `ANOMALY_DETECTOR_ZSCORE_THRESHOLD` | Z-Score 阈值 | `3.0` |
| `ANOMALY_DETECTOR_MIN_DATA_POINTS` | 最小数据点数量 | `20` |
| `ANOMALY_DETECTOR_HISTORY_SIZE` | 历史数据保留数量 | `1000` |
| `ANOMALY_DETECTOR_QUERY_RANGE` | Prometheus 查询时间范围（秒） | `3600` |

### 指标过滤配置

| 环境变量 | 描述 | 默认值 |
|---------|------|-------|
| `ANOMALY_DETECTOR_MAX_METRICS` | 最大监控指标数量（0=无限制） | `100` |
| `ANOMALY_DETECTOR_INCLUDE_METRICS` | 包含的指标正则（逗号分隔） | (空) |
| `ANOMALY_DETECTOR_EXCLUDE_METRICS` | 排除的指标正则（逗号分隔） | `^go_.*,^process_.*,^promhttp_.*` |

## 使用示例

### 最小配置

```bash
# 设置 Prometheus 地址
export PROMETHEUS_URL="http://prometheus:9090"

# 启动 Keep (服务会自动启动)
python -m keep.server_jobs_bg
```

### 完整配置示例

```bash
# Prometheus 配置
export PROMETHEUS_URL="http://prometheus:9090"
export PROMETHEUS_USERNAME="admin"
export PROMETHEUS_PASSWORD="secret"

# Keep API 配置
export KEEP_API_URL="http://keep-api:8080"
export KEEP_API_KEY="your-api-key"

# 算法配置
export ANOMALY_DETECTOR_ENABLED="true"
export ANOMALY_DETECTOR_INTERVAL="300"
export ANOMALY_DETECTOR_ALGORITHM="both"
export ANOMALY_DETECTOR_CONTAMINATION="0.05"
export ANOMALY_DETECTOR_ZSCORE_THRESHOLD="3.0"

# 只监控特定指标
export ANOMALY_DETECTOR_INCLUDE_METRICS="^http_.*,^cpu_.*,^memory_.*"
```

### Docker Compose 示例

```yaml
services:
  keep-api:
    # ... existing keep configuration ...
    
  keep-background-jobs:
    image: keep-api
    command: python -m keep.server_jobs_bg
    environment:
      # Prometheus
      - PROMETHEUS_URL=http://prometheus:9090
      
      # Keep API
      - KEEP_API_URL=http://keep-api:8080
      - KEEP_API_KEY=${KEEP_API_KEY}
      
      # Anomaly Detector
      - ANOMALY_DETECTOR_ENABLED=true
      - ANOMALY_DETECTOR_INTERVAL=300
      - ANOMALY_DETECTOR_ALGORITHM=both
    depends_on:
      - keep-api
      - prometheus
```

## 告警格式

检测到异常时，服务会向 Keep 发送 Prometheus Alertmanager 格式的告警：

```json
{
  "alerts": [{
    "status": "firing",
    "labels": {
      "alertname": "AnomalyDetected_http_requests_total",
      "severity": "warning",
      "metric": "http_requests_total",
      "detection_method": "combined",
      "source": "anomaly-detector"
    },
    "annotations": {
      "summary": "异常检测：指标 http_requests_total 出现异常模式",
      "description": "AI 异常检测算法发现指标 http_requests_total 的值出现异常..."
    },
    "startsAt": "2024-01-01T00:00:00Z",
    "fingerprint": "abc123..."
  }]
}
```

## 算法说明

### Isolation Forest (孤立森林)

孤立森林是一种无监督的机器学习算法，特别适合异常检测：

- **原理**: 异常数据点更容易被"隔离"（需要更少的分割步骤）
- **优点**: 不需要标签数据，对高维数据效果好
- **参数**: `contamination` - 预期的异常比例（默认 0.05 = 5%）

### Z-Score (3σ 规则)

基于统计学的异常检测方法：

- **原理**: 计算每个数据点与均值的标准差倍数
- **3σ 规则**: 如果 Z-score > 3，数据点被认为是异常
- **优点**: 简单直观，计算快速

### Combined (组合检测)

同时使用多种算法，任一算法检测到异常即报告：

- 提高检测覆盖率
- 减少漏报
- 可能增加误报（可通过调整阈值平衡）

## 故障排查

### 服务未启动

1. 检查 `ANOMALY_DETECTOR_ENABLED` 是否为 `true`
2. 检查日志中的错误信息
3. 确认 Prometheus 地址可访问

### 没有检测到异常

1. 检查指标数据点是否足够（至少需要 20 个）
2. 检查指标是否被 exclude 规则过滤
3. 尝试降低 `ANOMALY_DETECTOR_ZSCORE_THRESHOLD`

### 误报过多

1. 提高 `ANOMALY_DETECTOR_ZSCORE_THRESHOLD`（例如从 3.0 到 4.0）
2. 降低 `ANOMALY_DETECTOR_CONTAMINATION`（例如从 0.05 到 0.01）

## 独立运行测试

```bash
cd keep
python -m keep.anomaly_detector.anomaly_detector_service
```

这将启动服务并打印日志，按 Ctrl+C 停止。

