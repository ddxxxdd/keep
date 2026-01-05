# Research: Anomaly Detector Provider Extension

**Date**: 2025-12-29  
**Feature**: 001-anomaly-detector-provider  
**Purpose**: 解决将异常检测扩展到 Tempo traces 和 Loki logs 的技术选择问题

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

## Summary

所有技术选择已确定：

1. **Tempo**: 使用 TraceQL 查询 trace，按时间窗口聚合为时间序列
2. **Loki**: 使用 LogQL 聚合查询，直接返回时间序列数据
3. **算法复用**: 统一返回 `np.ndarray`，直接使用现有算法模块
4. **查询接口**: 通过 `data_source` 参数区分数据源，`metric` 统一为查询表达式
5. **配置扩展**: 在 Provider 配置中添加 Tempo/Loki 连接字段，保持结构一致性

所有决策均基于现有 Keep 架构和最佳实践，最小化代码变更和用户学习成本。

