# Quick Start: 异常检测日志记录功能

**Date**: 2025-01-27  
**Feature**: 002-anomaly-detection-logs

## Overview

本功能为 Anomaly Detector Provider 添加完整的日志记录功能，确保无论检测结果如何（正常、异常、无数据、数据不足等），都能在日志系统中看到完整的异常检测执行信息。

## 功能特性

- ✅ 完整的日志记录：记录检测开始、查询执行、数据获取、检测算法执行、检测完成等所有阶段
- ✅ 结构化日志：使用 JSON 格式（生产环境）或开发终端格式（本地开发），包含丰富的结构化字段
- ✅ 上下文信息：自动包含工作流 ID、步骤 ID、租户 ID 等上下文信息，便于过滤和查询
- ✅ 多级别日志：INFO 用于正常流程，DEBUG 用于详细调试，WARNING 用于非致命问题，ERROR 用于严重错误
- ✅ 性能优化：日志记录不影响异常检测的执行性能，性能开销不超过 5%

## 使用方式

### 自动启用

日志记录功能在 Anomaly Detector Provider 中自动启用，无需额外配置。当你在工作流中调用该 Provider 时，所有异常检测执行都会自动记录日志。

### 查看日志

#### 1. 通过日志聚合系统（推荐）

如果 Keep 系统已配置 Loki、ELK 或 Grafana 等日志聚合系统，可以通过日志查询语言查询异常检测日志：

**LogQL 查询示例（Loki）**:

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

#### 2. 通过 Keep 前端界面

如果 Keep 前端界面提供了日志查看功能，可以在工作流执行详情页面查看异常检测步骤的详细日志。

#### 3. 通过容器日志

如果使用 Docker 部署，可以通过容器日志查看：

```bash
# 查看 Keep 后端容器日志
docker logs keep-backend-dev | grep "anomaly_detector"

# 实时查看日志
docker logs -f keep-backend-dev | grep "异常检测"
```

## 日志格式

### JSON 格式（生产环境）

当 `LOG_FORMAT=open_telemetry` 时，日志使用 JSON 格式：

```json
{
  "timestamp": "2025-01-27T10:30:45.123Z",
  "severity": "INFO",
  "message": "异常检测完成: 状态=normal, 总数据点=120, 异常数量=0",
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

当 `LOG_FORMAT=dev_terminal` 时，日志使用开发终端格式：

```
2025-01-27 10:30:45,123 - MainThread INFO - 异常检测完成: 状态=normal, 总数据点=120, 异常数量=0 [provider_type: anomaly_detector] [workflow_id: workflow-123] [metric: keep_http_server_duration_seconds_bucket] [status: normal] [total_points: 120] [anomaly_count: 0]
```

## 日志级别

### INFO 级别

记录正常执行流程和结果：
- 检测开始
- 查询执行
- 数据获取成功
- 检测完成（无论结果如何）

### DEBUG 级别

记录详细的执行细节（需要设置 `LOG_LEVEL=DEBUG`）：
- 算法执行细节（基线计算、阈值比较等）
- 数据统计信息（最小值、最大值等）
- 查询时间范围详情

### WARNING 级别

记录非致命性问题：
- 数据不足
- 查询返回空结果
- 配置问题

### ERROR 级别

记录严重错误：
- 数据源连接失败
- 查询超时
- 数据解析错误
- 其他未预期的异常

## 配置

### 日志级别

通过环境变量 `LOG_LEVEL` 控制日志级别：

```bash
# 设置为 INFO（默认，推荐生产环境）
LOG_LEVEL=INFO

# 设置为 DEBUG（用于详细调试）
LOG_LEVEL=DEBUG
```

### 日志格式

通过环境变量 `LOG_FORMAT` 控制日志格式：

```bash
# JSON 格式（生产环境，推荐）
LOG_FORMAT=open_telemetry

# 开发终端格式（本地开发）
LOG_FORMAT=dev_terminal
```

## 示例工作流

以下是一个使用 Anomaly Detector Provider 的示例工作流：

```yaml
workflows:
  - id: anomaly-detection-example
    name: 异常检测示例
    triggers:
      - type: alert
    steps:
      - name: 检测 HTTP 服务器延迟异常
        provider:
          type: anomaly_detector
          config: "{{ providers.anomaly_detector }}"
        with:
          metric: "keep_http_server_duration_seconds_bucket"
          time_range: "1h"
```

执行此工作流后，你可以在日志系统中看到以下日志：

1. **检测开始**: `"开始执行异常检测: metric=keep_http_server_duration_seconds_bucket, data_source=prometheus, time_range=1h"`
2. **查询执行**: `"执行 Prometheus 查询: rate(keep_http_server_duration_seconds_bucket[3m])"`
3. **数据获取**: `"从 Prometheus 获取到 120 个数据点"`
4. **检测完成**: `"异常检测完成: 状态=normal, 总数据点=120, 异常数量=0"`

## 常见问题

### Q: 为什么看不到日志？

A: 请检查以下事项：
1. 确认 `LOG_LEVEL` 设置正确（至少为 INFO）
2. 确认日志聚合系统（如 Loki）正常运行
3. 确认工作流已执行异常检测步骤
4. 检查日志查询语句是否正确

### Q: 如何查看详细的调试信息？

A: 设置 `LOG_LEVEL=DEBUG` 环境变量，然后重新执行工作流。DEBUG 级别会记录算法执行细节、数据统计信息等。

### Q: 日志会影响性能吗？

A: 不会。日志记录使用 Python 标准 logging 模块的异步处理，性能开销不超过检测总执行时间的 5%。

### Q: 如何过滤特定工作流的日志？

A: 使用日志查询语言，例如在 Loki 中：
```logql
{provider_type="anomaly_detector", workflow_id="your-workflow-id"}
```

### Q: 日志会包含敏感信息吗？

A: 日志只包含检测参数和结果信息，不包含敏感数据（如密码、Token 等）。Provider 配置中的敏感字段（如 `prometheus_password`）不会记录到日志中。

## 下一步

- 查看 [规范文档](./spec.md) 了解完整的功能需求
- 查看 [实现计划](./plan.md) 了解技术实现细节
- 查看 [数据模型](./data-model.md) 了解日志数据结构
- 查看 [API 合约](./contracts/logging-api.md) 了解日志接口规范

