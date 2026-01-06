# Research: Anomaly Detector Provider Extension

**Date**: 2025-12-29  
**Updated**: 2025-01-27  
**Feature**: 001-anomaly-detector-provider  
**Purpose**: 解决将异常检测扩展到 Tempo traces 和 Loki logs 的技术选择问题，以及实现自动告警发送机制

## Research Questions

### Q1: Tempo API 查询方式与数据格式

**Question**: 如何从 Tempo 查询时间序列数据用于异常检测？

**Decision**: 使用 Tempo 的 TraceQL 查询语言，通过 `/api/search` 或 `/api/traces` 端点获取 trace 数据，然后聚合为时间序列（例如：按时间窗口统计 trace 数量、平均延迟、错误率等）。

**Rationale**: 
- Tempo 主要存储 trace 数据，不直接提供 Prometheus 风格的时间序列
- 需要通过 TraceQL 查询 trace，然后按时间窗口聚合为可检测的数值序列
- 常见聚合维度：trace 数量、平均/分位数延迟、错误 trace 比例

**Alternatives Considered**:
- 直接使用 Tempo 的 metrics 导出：Tempo 本身不提供内置的 metrics 导出，需要额外配置
- 通过 Prometheus 查询 Tempo 导出的 metrics：需要 Tempo 配置 metrics 导出，增加了依赖复杂度

**References**:
- Tempo API 文档：https://grafana.com/docs/tempo/latest/api_docs/
- TraceQL 语法：https://grafana.com/docs/tempo/latest/traceql/

---

### Q2: Loki API 查询方式与数据格式

**Question**: 如何从 Loki 查询时间序列数据用于异常检测？

**Decision**: 使用 Loki 的 LogQL 查询语言，通过 `/loki/api/v1/query_range` 端点执行聚合查询（如 `rate()`, `count_over_time()`），直接返回时间序列数据。

**Rationale**:
- Loki 的 LogQL 支持 Prometheus 风格的聚合函数，可以直接返回时间序列
- 示例查询：`sum(rate({job="varlogs"}[5m])) by (level)` 返回按日志级别聚合的速率时间序列
- 与 Prometheus 查询格式兼容，便于复用现有的时间序列处理逻辑

**Alternatives Considered**:
- 查询原始日志然后本地聚合：效率低，不适合大规模日志数据
- 使用 Loki 的 metrics 查询接口：LogQL 的 `query_range` 已经提供了 metrics 查询能力

**References**:
- Loki LogQL 文档：https://grafana.com/docs/loki/latest/logql/
- Loki API 文档：https://grafana.com/docs/loki/latest/api/

---

### Q3: 统一异常检测算法接口

**Question**: 如何使现有的异常检测算法（Isolation Forest、Z-Score）能够处理来自不同数据源的时间序列？

**Decision**: 所有数据源客户端（Prometheus/Tempo/Loki）统一返回 `numpy.ndarray` 格式的时间序列数据，直接传入现有的 `BaseAnomalyDetector.detect()` 方法。

**Rationale**:
- 现有的 `keep.anomaly_detector.algorithms` 模块已经设计为接受 `np.ndarray`，与数据源无关
- 只需要在数据源客户端层做数据转换，算法层无需修改
- 保持代码复用性和一致性

**Alternatives Considered**:
- 为每种数据源实现独立的检测逻辑：会导致代码重复，维护成本高
- 抽象数据源接口：当前需求下过度设计，numpy 数组已经足够通用

**Implementation Notes**:
- Prometheus: 直接返回 `query_range` 的数值数组
- Tempo: 查询 trace 后按时间窗口聚合为数值数组（例如：每 5 分钟 trace 数量）
- Loki: 使用 LogQL 聚合查询直接返回数值数组

---

### Q4: Provider 查询参数设计

**Question**: 如何在 Provider 的 `_query()` 方法中区分要查询的数据源类型（Prometheus/Tempo/Loki）？

**Decision**: 在 `with` 参数中添加 `data_source` 字段（可选，默认为 "prometheus"），支持值：`"prometheus"`, `"tempo"`, `"loki"`。同时保留 `metric` 参数，对于 Tempo/Loki 则解释为 TraceQL/LogQL 查询语句。

**Rationale**:
- 保持与现有 Prometheus 查询的兼容性（默认行为不变）
- 通过显式指定 `data_source` 来切换数据源，语义清晰
- `metric` 参数可以统一为"查询表达式"的概念（PromQL/TraceQL/LogQL）

**Example Usage**:
```yaml
# Prometheus 查询（默认）
with:
  metric: "keep_http_server_duration_seconds_bucket"

# Tempo 查询
with:
  data_source: "tempo"
  metric: '{ .service_name = "frontend" } | count() by (status_code)'

# Loki 查询
with:
  data_source: "loki"
  metric: 'sum(rate({job="varlogs"}[5m])) by (level)'
```

**Alternatives Considered**:
- 为每种数据源创建独立的 Provider：增加用户配置复杂度，不符合"统一异常检测"的设计目标
- 通过 `metric` 参数前缀自动识别（如 `tempo:...`）：不够直观，容易误用

---

### Q5: 配置参数扩展

**Question**: 如何在 Provider 配置中添加 Tempo 和 Loki 的连接信息？

**Decision**: 扩展 `AnomalyDetectorProviderAuthConfig`，添加 `tempo_url`, `tempo_enabled`, `loki_url`, `loki_enabled` 等字段，与现有的 `prometheus_url` 保持一致的结构。

**Rationale**:
- 保持配置结构的一致性，便于用户理解和迁移
- 支持可选启用（通过 `*_enabled` 字段），用户可以选择只配置需要的 data source
- 复用现有的 `TempoConfig` 和 `LokiConfig` 类（已在 `keep.anomaly_detector.config` 中定义）

**Configuration Structure**:
```python
@dataclasses.dataclass
class AnomalyDetectorProviderAuthConfig:
    # Prometheus (required)
    prometheus_url: str
    prometheus_username: str = ""
    prometheus_password: str = ""
    prometheus_verify_ssl: bool = True
    
    # Tempo (optional)
    tempo_url: str = "http://tempo:3200"
    tempo_enabled: bool = False
    
    # Loki (optional)
    loki_url: str = "http://loki:3100"
    loki_enabled: bool = False
    
    # 检测参数（通用）
    algorithm: str = "both"
    min_data_points: int = 20
    # ... 其他参数
```

**Alternatives Considered**:
- 使用嵌套配置对象：增加序列化/反序列化复杂度，当前扁平结构已足够
- 环境变量映射：Provider 配置通过 UI 管理，不需要环境变量

---

---

### Q6: 自动告警发送机制

**Question**: 如何在 Provider 中实现自动发送告警到 Keep 平台，模拟旧独立服务的自动告警行为？

**Decision**: 在 `_query` 方法中，当检测到异常数量超过配置的阈值时，直接调用 `keep.api.tasks.process_event_task.process_event` 函数发送告警。

**Rationale**:
- `process_event` 是 Keep 平台内部处理告警的标准函数，负责告警的去重、存储、通知等完整流程
- 直接调用 `process_event` 可以确保告警正确处理，利用 Keep 平台的去重机制
- 与旧服务行为一致：旧服务也是直接调用 Keep API 发送告警
- 无需通过工作流，减少用户配置复杂度

**Implementation Details**:
```python
from keep.api.tasks.process_event_task import process_event
from keep.api.models.alert import AlertDto, AlertStatus, AlertSeverity
from keep.providers.base.base_provider import BaseProvider
import hashlib
import json
from datetime import datetime, timezone

# 在 _query 方法中，检测到异常后：
if anomaly_count >= min_anomaly_count_for_alert:
    alert = AlertDto(
        name=f"异常检测告警: {metric_name}",
        status=AlertStatus.FIRING,
        severity=AlertSeverity.WARNING,  # 可根据异常数量或评分调整
        lastReceived=datetime.now(timezone.utc).isoformat(),
        message=f"检测到 {anomaly_count} 个异常点",
        description=f"指标 {metric_name} 在检测时间范围内发现异常...",
        labels={
            "metric": metric_name,
            "anomaly_count": str(anomaly_count),
            "algorithm": algorithm,
            # ... 其他标签
        },
        fingerprint=self._generate_fingerprint(metric_name, labels),
        source=["anomaly_detector"],
        environment="production",  # 可从配置获取
    )
    
    try:
        process_event(
            ctx={},  # 空上下文，因为不是从工作流调用
            tenant_id=self.context_manager.tenant_id,
            provider_type="anomaly_detector",
            provider_id=self.provider_id,
            fingerprint=alert.fingerprint,
            api_key_name=None,
            trace_id=None,
            event=alert,
        )
    except Exception as e:
        logger.error("Failed to send alert", exc_info=True, extra={
            "metric": metric_name,
            "anomaly_count": anomaly_count,
        })
        # 不抛出异常，确保检测结果正常返回
```

**Alternatives Considered**:
- 通过 HTTP API 调用 `/alerts/event` 端点：需要处理认证、网络错误等，复杂度更高
- 通过工作流发送告警：增加用户配置复杂度，不符合"与旧服务效果一样"的需求
- 使用消息队列：过度设计，`process_event` 已经支持异步处理

**References**:
- `keep/api/tasks/process_event_task.py`: `process_event` 函数实现
- `keep/api/models/alert.py`: `AlertDto` 数据模型
- `keep/providers/keep_provider/keep_provider.py`: Keep Provider 中调用 `process_event` 的示例

---

### Q7: 告警 Fingerprint 生成策略

**Question**: 如何为自动发送的告警生成合适的 fingerprint，以便 Keep 平台能够正确进行告警去重？

**Decision**: 使用 `BaseProvider.get_alert_fingerprint()` 方法，基于指标名称和关键标签（如 `metric`, `data_source` 等）生成 fingerprint。

**Rationale**:
- Keep 平台使用 fingerprint 进行告警去重，相同 fingerprint 的告警会被合并
- 基于指标名称和关键标签可以确保相同指标的异常告警被正确去重
- 复用 `BaseProvider` 的现有方法，保持一致性

**Implementation**:
```python
def _generate_fingerprint(self, metric_name: str, labels: dict) -> str:
    """生成告警 fingerprint"""
    # 构建 fingerprint 字段列表
    fingerprint_fields = ["name", "labels.metric"]
    
    # 如果有其他关键标签（如 instance, job），也加入
    if "instance" in labels:
        fingerprint_fields.append("labels.instance")
    if "job" in labels:
        fingerprint_fields.append("labels.job")
    
    # 创建临时 AlertDto 用于计算 fingerprint
    temp_alert = AlertDto(
        name=f"异常检测告警: {metric_name}",
        labels=labels,
    )
    
    return BaseProvider.get_alert_fingerprint(temp_alert, fingerprint_fields)
```

**Alternatives Considered**:
- 仅基于指标名称：可能导致不同实例的异常被错误去重
- 基于所有标签：可能导致相同指标的不同时间点的异常无法去重
- 手动计算 SHA-256：不必要，`get_alert_fingerprint` 已经提供了标准实现

**References**:
- `keep/providers/base/base_provider.py`: `get_alert_fingerprint` 方法
- `keep/api/models/alert.py`: `get_fingerprint` 辅助函数

---

### Q8: 异常数量阈值配置

**Question**: 如何实现可配置的异常数量阈值，控制告警发送频率？

**Decision**: 在 `AnomalyDetectorProviderAuthConfig` 中添加 `min_anomaly_count_for_alert` 字段，默认值为 1。

**Rationale**:
- 可配置的阈值提供灵活性，用户可以根据场景调整告警敏感度
- 默认值为 1 确保与旧服务行为一致（旧服务检测到任何异常就发送告警）
- 通过 UI 配置，无需修改代码

**Configuration**:
```python
min_anomaly_count_for_alert: int = dataclasses.field(
    default=1,
    metadata={
        "description": "触发告警的最小异常数量阈值",
        "hint": "只有当检测到的异常数量 >= 此值时才会发送告警，默认值为 1",
    },
)
```

**Alternatives Considered**:
- 固定阈值（硬编码）：无法满足不同场景的需求
- 基于异常评分阈值：复杂度更高，当前需求下异常数量阈值已足够

---

## Summary

所有技术选择已确定：

1. **Tempo**: 使用 TraceQL 查询 trace，按时间窗口聚合为时间序列
2. **Loki**: 使用 LogQL 聚合查询，直接返回时间序列数据
3. **算法复用**: 统一返回 `np.ndarray`，直接使用现有算法模块
4. **查询接口**: 通过 `data_source` 参数区分数据源，`metric` 统一为查询表达式
5. **配置扩展**: 在 Provider 配置中添加 Tempo/Loki 连接字段，保持结构一致性
6. **自动告警发送**: 在 `_query` 方法中直接调用 `process_event` 函数发送告警，完全模拟旧服务行为
7. **告警 Fingerprint**: 使用 `BaseProvider.get_alert_fingerprint()` 方法，基于指标名称和关键标签生成
8. **异常数量阈值**: 在 Provider 配置中添加 `min_anomaly_count_for_alert` 字段，默认值为 1

所有决策均基于现有 Keep 架构和最佳实践，最小化代码变更和用户学习成本。

