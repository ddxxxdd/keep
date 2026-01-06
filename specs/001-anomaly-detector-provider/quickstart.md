# Quick Start: Anomaly Detector Provider

**Date**: 2025-12-29  
**Updated**: 2025-01-27  
**Feature**: 001-anomaly-detector-provider

## 概述

本文档提供 Anomaly Detector Provider 的快速入门指南，包括安装、配置和在工作流中使用。

## 前置要求

- Keep 平台已部署并运行
- 至少一个数据源可用：
  - Prometheus（必需）
  - Tempo（可选，用于 traces 异常检测）
  - Loki（可选，用于 logs 异常检测）

## 安装 Provider

### 步骤 1: 在 Keep UI 中打开 Providers 页面

1. 登录 Keep UI
2. 导航到左侧菜单的 **Providers** 选项
3. 在 **Available Providers** 部分找到 **Anomaly Detector**

### 步骤 2: 配置 Provider

点击 **Anomaly Detector** 卡片，进入配置页面：

#### 基础配置（必需）

- **Provider Name**: 输入 Provider 实例名称（如 `anomaly-detector-prod`）
- **Prometheus 服务地址**: 输入 Prometheus URL（如 `http://prometheus:9090`）
- **Prometheus 用户名**: （可选）如果 Prometheus 需要基本认证
- **Prometheus 密码**: （可选）如果 Prometheus 需要基本认证
- **是否校验 Prometheus SSL 证书**: 选择 `true` 或 `false`（自签名证书设为 `false`）

#### Tempo 配置（可选）

- **Tempo 服务地址**: 输入 Tempo URL（默认 `http://tempo:3200`）
- **启用 Tempo**: 勾选以启用 Tempo traces 异常检测

#### Loki 配置（可选）

- **Loki 服务地址**: 输入 Loki URL（默认 `http://loki:3100`）
- **启用 Loki**: 勾选以启用 Loki logs 异常检测

#### 检测参数配置（可选，使用默认值即可）

- **检测算法**: 选择 `isolation_forest`、`zscore` 或 `both`（默认 `zscore`，与旧服务一致）
- **参与检测的最小数据点数量**: 默认 `10`（与旧服务一致）
- **保留用于分析的历史数据点数量**: 默认 `1000`
- **Prometheus 查询时间范围（秒）**: 默认 `1800`（30分钟，与旧服务一致）
- **Prometheus 查询步长**: 默认 `15s`（与旧服务一致）
- **环比变化阈值**: 默认 `0.3`（+30%，与旧服务一致）
- **触发告警的最小异常数量阈值**: 默认 `1`（检测到任何异常即发送告警）

### 步骤 3: 保存并验证

1. 点击 **Save** 或 **Install** 按钮
2. 系统会自动验证配置（测试 Prometheus 连接）
3. 如果验证成功，Provider 会出现在 **Installed Providers** 列表中

## 在工作流中使用

### 示例 1: 检测 Prometheus 指标异常

创建一个工作流，检测 HTTP 请求延迟指标：

```yaml
workflow:
  id: detect-http-latency-anomaly
  name: Detect HTTP Latency Anomaly
  description: 检测 HTTP 服务器延迟异常
  triggers:
    - type: manual
  steps:
    - name: detect-anomalies
      provider:
        type: anomaly_detector
        config: "{{ providers.anomaly-detector-prod }}"
        with:
          metric: "keep_http_server_duration_seconds_bucket"
          time_range: "1h"
  actions:
    - name: send-alert-if-anomaly
      condition: "{{ steps.detect-anomalies.status == 'success' and steps.detect-anomalies.anomaly_count > 0 }}"
      provider:
        type: slack
        config: "{{ providers.slack }}"
        with:
          message: "检测到 {{ steps.detect-anomalies.anomaly_count }} 个异常点"
```

### 示例 2: 检测 Tempo Traces 异常

检测特定服务的 trace 数量异常：

```yaml
workflow:
  id: detect-trace-anomaly
  name: Detect Trace Anomaly
  description: 检测前端服务的 trace 数量异常
  triggers:
    - type: manual
  steps:
    - name: detect-trace-anomalies
      provider:
        type: anomaly_detector
        config: "{{ providers.anomaly-detector-prod }}"
        with:
          data_source: "tempo"
          metric: '{ .service_name = "frontend" } | count()'
          time_range: "30m"
```

### 示例 3: 检测 Loki Logs 异常

检测错误日志速率异常：

```yaml
workflow:
  id: detect-log-error-anomaly
  name: Detect Log Error Anomaly
  description: 检测错误日志速率异常
  triggers:
    - type: manual
  steps:
    - name: detect-log-anomalies
      provider:
        type: anomaly_detector
        config: "{{ providers.anomaly-detector-prod }}"
        with:
          data_source: "loki"
          metric: 'sum(rate({job="varlogs", level="error"}[5m]))'
          time_range: "1h"
```

## 推荐配置（复现旧服务效果）

如果要复现 `docker-compose-with-otel.yaml` 中被注释掉的异常检测服务效果，使用以下配置：

```
Provider Name: prometheus
Prometheus 服务地址: http://prometheus:9090
检测算法: zscore
参与检测的最小数据点数量: 10
Prometheus 查询时间范围（秒）: 1800
Prometheus 查询步长: 15s
环比变化阈值: 0.3
```

**注意**: 
- 上述配置值已经是 Provider 的默认值，无需手动修改即可复现旧服务效果
- `zscore_threshold`（Z-Score 阈值）当前硬编码为 3.0，无法通过 UI 配置（旧服务中为 2.0，如需更敏感可修改代码）
- `include_metrics` 和 `exclude_metrics` 过滤规则需要在工作流的 `metric` 参数中显式指定（例如：使用完整的 PromQL 表达式）

## 自动告警功能

Provider 在检测到异常时会自动向 Keep 平台发送告警，无需通过工作流即可触发告警。

### 告警触发条件

- 当检测到的异常数量 >= `min_anomaly_count_for_alert`（默认值为 1）时，Provider 会自动发送告警
- 告警包含完整的异常检测信息：指标名称、异常数量、异常点详情、检测算法、统计信息等
- 告警使用 Keep 平台的去重机制（基于 fingerprint），避免重复告警

### 告警内容

自动发送的告警包含以下信息：
- **告警名称**: 基于指标名称（如 "异常检测告警: keep_http_server_duration_seconds_bucket"）
- **告警描述**: 包含异常数量、检测算法、统计信息（均值、标准差）等
- **告警严重程度**: 根据异常情况自动设置（WARNING/CRITICAL）
- **异常点详情**: 每个异常点的时间戳、数值、评分等
- **检测时间**: 异常检测的执行时间

### 注意事项

- 告警发送失败不会影响异常检测结果的正常返回
- 相同指标的重复告警会被 Keep 平台自动去重（基于 fingerprint）
- 可以通过调整 `min_anomaly_count_for_alert` 来控制告警敏感度

## 常见问题

### Q: 如何知道检测是否成功？

A: 检查返回结果的 `status` 字段：
- `"success"`: 检测完成且发现异常
- `"normal"`: 检测完成但未发现异常
- `"no_data"`: 查询无数据
- `"insufficient_data"`: 数据点不足

### Q: 如何在工作流中根据检测结果做条件判断？

A: 使用工作流的 `condition` 字段：

```yaml
actions:
  - name: alert
    condition: "{{ steps.detect-anomalies.status == 'success' and steps.detect-anomalies.anomaly_count > 0 }}"
    provider:
      type: slack
      # ...
```

### Q: 支持哪些 PromQL/TraceQL/LogQL 查询？

A: 
- **Prometheus**: 支持所有 PromQL 语法，Provider 会自动为计数型指标包装 `rate()`
- **Tempo**: 支持 TraceQL 语法，参考 [Tempo TraceQL 文档](https://grafana.com/docs/tempo/latest/traceql/)
- **Loki**: 支持 LogQL 语法，参考 [Loki LogQL 文档](https://grafana.com/docs/loki/latest/logql/)

### Q: 如何调试查询失败？

A: 
1. 检查 Provider 配置中的数据源 URL 是否正确
2. 检查数据源是否可访问（网络连接、认证等）
3. 查看工作流执行日志，查找错误信息
4. 验证查询表达式语法是否正确

### Q: 自动告警是如何工作的？

A: 
- Provider 在执行 `_query` 方法时，如果检测到异常数量 >= `min_anomaly_count_for_alert`，会自动调用 Keep 平台的 `process_event` 函数发送告警
- 告警会出现在 Keep 前端界面的告警列表中
- 告警使用 fingerprint 进行去重，相同指标的重复告警会被合并
- 告警发送失败不会影响异常检测结果的正常返回

### Q: 如何控制告警频率？

A: 
- 调整 `min_anomaly_count_for_alert` 参数：设置更高的值可以减少告警频率（例如设置为 3，则只有检测到 3 个或更多异常时才发送告警）
- Keep 平台的去重机制会自动处理相同指标的重复告警

## 下一步

- 查看 [数据模型文档](./data-model.md) 了解详细的数据结构
- 查看 [API 契约文档](./contracts/provider-api.md) 了解完整的接口定义
- 参考 [实现计划](./plan.md) 了解技术实现细节

