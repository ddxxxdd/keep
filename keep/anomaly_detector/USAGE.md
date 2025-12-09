# 异常检测器使用说明

## 功能概述

异常检测器现在支持三类数据源的异常检测：
1. **Metrics（指标数据）** - 来自 Prometheus
2. **Traces（追踪数据）** - 来自 Tempo
3. **Logs（日志数据）** - 来自 Loki

## 配置说明

### 环境变量配置

#### 基础配置
- `ANOMALY_DETECTOR_ENABLED`: 启用/禁用异常检测服务（默认: `true`）
- `ANOMALY_DETECTOR_INTERVAL`: 检测间隔（秒，默认: `300`）
- `ANOMALY_DETECTOR_ALGORITHM`: 检测算法（`isolation_forest`, `zscore`, `both`，默认: `both`）

#### Prometheus 配置（Metrics）
- `PROMETHEUS_URL`: Prometheus 地址（默认: `http://localhost:9090`）
- `PROMETHEUS_USERNAME`: Prometheus 用户名（可选）
- `PROMETHEUS_PASSWORD`: Prometheus 密码（可选）

#### Tempo 配置（Traces）
- `TEMPO_URL`: Tempo 地址（默认: `http://tempo:3200`）
- `ANOMALY_DETECTOR_TRACES_ENABLED`: 启用 Traces 检测（默认: `false`）

#### Loki 配置（Logs）
- `LOKI_URL`: Loki 地址（默认: `http://loki:3100`）
- `ANOMALY_DETECTOR_LOGS_ENABLED`: 启用 Logs 检测（默认: `false`）

#### Keep API 配置
- `KEEP_API_URL`: Keep API 地址（默认: `http://localhost:8080`）
- `KEEP_API_KEY`: Keep API 密钥（可选）

### Docker Compose 配置示例

```yaml
keep-anomaly-detector:
  environment:
    # 基础配置
    - ANOMALY_DETECTOR_ENABLED=true
    - ANOMALY_DETECTOR_INTERVAL=15
    
    # Prometheus 配置
    - PROMETHEUS_URL=http://prometheus:9090
    
    # 启用 Traces 检测
    - ANOMALY_DETECTOR_TRACES_ENABLED=true
    - TEMPO_URL=http://tempo:3200
    
    # 启用 Logs 检测
    - ANOMALY_DETECTOR_LOGS_ENABLED=true
    - LOKI_URL=http://loki:3100
    
    # Keep API 配置
    - KEEP_API_URL=http://keep-backend-dev:8080
    - KEEP_API_KEY=your-api-key
```

## API 端点

### 1. 导出告警数据

**端点**: `GET /alerts/anomaly-detector/export`

**参数**:
- `start_time` (可选): 开始时间过滤（ISO 格式）
- `end_time` (可选): 结束时间过滤（ISO 格式）
- `source_type` (可选): 数据源类型过滤（`metrics`, `traces`, `logs`）
- `format` (可选): 导出格式（`json` 或 `csv`，默认: `json`）

**示例**:
```bash
# 导出所有告警（JSON 格式）
curl http://localhost:8080/alerts/anomaly-detector/export?format=json

# 导出 Metrics 告警（CSV 格式）
curl http://localhost:8080/alerts/anomaly-detector/export?source_type=metrics&format=csv

# 导出指定时间范围的告警
curl "http://localhost:8080/alerts/anomaly-detector/export?start_time=2024-01-01T00:00:00Z&end_time=2024-01-02T00:00:00Z"
```

**响应**: 返回文件下载，文件保存在 `alert_exports/` 目录下

### 2. 获取告警统计信息

**端点**: `GET /alerts/anomaly-detector/statistics`

**示例**:
```bash
curl http://localhost:8080/alerts/anomaly-detector/statistics
```

**响应**:
```json
{
  "total_alerts": 150,
  "by_source_type": {
    "metrics": 100,
    "traces": 30,
    "logs": 20
  },
  "by_algorithm": {
    "rate_change": 150
  },
  "recent_alerts": [
    {
      "timestamp": "2024-01-15T10:30:00Z",
      "source_type": "metrics",
      "identifier": "http_request_duration",
      "anomaly_count": 5
    }
  ]
}
```

## 告警格式

所有告警都包含以下信息：
- **数据源类型** (`data_source`): `metrics`, `traces`, 或 `logs`
- **检测时间**: 异常检测的时间戳
- **检测算法**: 使用的检测算法
- **异常详情**: 包括基线值、异常值、变化比例等

### Metrics 告警示例
```json
{
  "labels": {
    "alertname": "AnomalyDetected_Metrics_http_request_duration",
    "data_source": "metrics",
    "metric": "http_request_duration",
    "detection_method": "rate_change"
  },
  "annotations": {
    "summary": "异常检测：metrics 数据源 http_request_duration 出现异常模式",
    "description": "AI 异常检测算法发现指标 http_request_duration 的值出现异常。\n\n【数据源类型】Metrics (指标数据)\n..."
  }
}
```

### Traces 告警示例
```json
{
  "labels": {
    "alertname": "AnomalyDetected_Traces_trace_duration",
    "data_source": "traces",
    "service": "trace_duration",
    "detection_method": "rate_change"
  },
  "annotations": {
    "summary": "异常检测：traces 数据源 trace_duration 出现异常模式",
    "description": "AI 异常检测算法发现追踪数据 trace_duration 出现异常。\n\n【数据源类型】Traces (追踪数据)\n..."
  }
}
```

### Logs 告警示例
```json
{
  "labels": {
    "alertname": "AnomalyDetected_Logs_log_error_count",
    "data_source": "logs",
    "log_source": "log_error_count",
    "detection_method": "rate_change"
  },
  "annotations": {
    "summary": "异常检测：logs 数据源 log_error_count 出现异常模式",
    "description": "AI 异常检测算法发现日志数据 log_error_count 出现异常。\n\n【数据源类型】Logs (日志数据)\n..."
  }
}
```

## 三类数据的含义

### Metrics（指标数据）
- **来源**: Prometheus
- **检测内容**: 指标值的异常变化（如 CPU 使用率、请求延迟等）
- **检测方法**: 基于基线值的百分比变化检测

### Traces（追踪数据）
- **来源**: Tempo
- **检测内容**: 
  - 追踪持续时间异常
  - 错误率异常
- **检测方法**: 基于追踪持续时间和错误率的统计检测

### Logs（日志数据）
- **来源**: Loki
- **检测内容**:
  - 错误日志数量异常
  - 日志量异常
- **检测方法**: 基于错误日志频率和日志量的统计检测

## 故障注入检测

异常检测器可以检测到故障注入产生的异常模式。当检测到异常时，告警信息中会包含：
- 检测时间
- 基线值（正常状态）
- 异常值（故障状态）
- 变化比例

通过对比故障注入开启时间和检测时间，可以判断异常是否为故障注入产生的。

## 注意事项

1. **数据源连接**: 确保 Prometheus、Tempo、Loki 服务正常运行且可访问
2. **数据量**: 检测需要足够的数据点（默认至少 20 个），系统启动初期可能无法检测
3. **性能影响**: 启用三类数据检测会增加系统负载，建议根据实际需求选择性启用
4. **告警去重**: 系统会自动去重，避免同一异常重复告警
5. **导出文件**: 导出的文件保存在 `alert_exports/` 目录，建议定期清理

## 故障排查

### 检查服务状态
```bash
# 检查异常检测器是否运行
curl http://localhost:8080/alerts/anomaly-detector/statistics
```

### 查看日志
异常检测器的日志会输出到标准输出，包含：
- 检测周期开始/结束
- 检测到的异常
- 告警发送状态
- 错误信息

### 常见问题

1. **没有检测到异常**
   - 检查数据源是否正常
   - 确认有足够的数据点
   - 检查阈值配置是否合理

2. **告警过多**
   - 调整 `ANOMALY_DETECTOR_RATE_CHANGE_THRESHOLD` 阈值
   - 增加 `ANOMALY_DETECTOR_MIN_DATA_POINTS` 最小数据点数

3. **无法连接数据源**
   - 检查网络连接
   - 确认 URL 配置正确
   - 检查认证信息（如需要）

