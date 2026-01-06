# Research: 异常检测日志记录功能

**Date**: 2025-01-27  
**Feature**: 002-anomaly-detection-logs  
**Purpose**: 解决如何为 Anomaly Detector Provider 添加完整日志记录功能的技术选择问题

## Research Questions

### Q1: Keep 项目的日志标准和格式

**Question**: Keep 项目使用什么日志格式和标准？如何确保新增的日志与现有日志保持一致？

**Decision**: 使用 Python 标准 `logging` 模块，通过 `extra` 参数添加结构化字段。Keep 项目支持两种日志格式：
- `open_telemetry`: JSON 格式（使用 `pythonjsonlogger`），适用于生产环境
- `dev_terminal`: 开发终端格式，适用于本地开发

**Rationale**: 
- Keep 项目已经建立了完整的日志基础设施（`keep/api/logging.py`）
- 使用 `extra` 参数可以添加结构化字段，这些字段会被 JSON 格式化器自动包含
- 日志会自动通过 `WorkflowContextFilter` 添加上下文信息（workflow_id, workflow_execution_id, tenant_id, step_id）
- 保持与现有日志格式的一致性，便于日志聚合系统解析和查询

**Alternatives Considered**:
- 创建自定义日志格式：不符合 Keep 项目的日志标准，增加维护成本
- 使用第三方日志库：Keep 项目已使用标准 logging 模块，无需引入新依赖

**References**:
- Keep 日志配置：`keep/api/logging.py`
- 日志格式配置：通过 `LOG_FORMAT` 环境变量控制（`open_telemetry` 或 `dev_terminal`）

---

### Q2: 如何获取工作流上下文信息

**Question**: 如何从 Provider 中获取工作流 ID、步骤 ID、租户 ID 等上下文信息，以便添加到日志中？

**Decision**: 通过 `ContextManager` 对象获取上下文信息。`ContextManager` 在 Provider 初始化时通过 `BaseProvider.__init__()` 传入，包含以下属性：
- `context_manager.workflow_id`: 工作流 ID
- `context_manager.workflow_execution_id`: 工作流执行 ID
- `context_manager.tenant_id`: 租户 ID
- 步骤 ID 需要通过线程上下文获取（`threading.current_thread().step_id`）

**Rationale**:
- `ContextManager` 是 Keep 项目中传递上下文信息的标准方式
- Provider 已经通过 `self.context_manager` 访问上下文信息
- 步骤 ID 通过线程上下文传递，符合 Keep 的架构设计

**Alternatives Considered**:
- 通过环境变量传递：不符合 Keep 的架构设计，上下文信息应该通过对象传递
- 通过日志过滤器自动添加：`WorkflowContextFilter` 已经会自动添加这些信息，但我们需要在日志消息中明确包含以便查询

**Implementation Notes**:
```python
# 在 Provider 中获取上下文信息
workflow_id = self.context_manager.workflow_id
workflow_execution_id = self.context_manager.workflow_execution_id
tenant_id = self.context_manager.tenant_id

# 步骤 ID 需要通过线程上下文获取
import threading
step_id = getattr(threading.current_thread(), "step_id", None)
```

---

### Q3: 结构化日志字段设计

**Question**: 异常检测日志应该包含哪些结构化字段，以便在日志系统中进行过滤和查询？

**Decision**: 日志应包含以下结构化字段（通过 `extra` 参数传递）：
- **必需字段**（用于过滤和查询）：
  - `provider_type`: `"anomaly_detector"`
  - `workflow_id`: 工作流 ID（如果可用）
  - `workflow_execution_id`: 工作流执行 ID（如果可用）
  - `step_id`: 步骤 ID（如果可用）
  - `tenant_id`: 租户 ID
- **检测参数字段**：
  - `metric`: 查询的指标/查询表达式
  - `data_source`: 数据源类型（prometheus/tempo/loki）
  - `time_range`: 查询时间范围
  - `algorithm`: 检测算法类型
- **执行结果字段**：
  - `status`: 检测状态（success/normal/no_data/insufficient_data）
  - `total_points`: 总数据点数量
  - `anomaly_count`: 异常数量
  - `mean`: 数据均值
  - `std`: 数据标准差

**Rationale**:
- 必需字段用于在日志系统中过滤和查询特定工作流或步骤的日志
- 检测参数字段帮助理解检测执行的上下文
- 执行结果字段提供检测结果的完整信息
- 字段命名与规范中的要求保持一致

**Alternatives Considered**:
- 将所有信息放在日志消息中：不利于日志系统解析和查询
- 使用嵌套结构：JSON 格式支持嵌套，但扁平结构更便于查询

**Example**:
```python
self.logger.info(
    "异常检测完成: 状态=normal, 总数据点=120, 异常数量=0",
    extra={
        "provider_type": "anomaly_detector",
        "workflow_id": workflow_id,
        "workflow_execution_id": workflow_execution_id,
        "step_id": step_id,
        "tenant_id": tenant_id,
        "metric": metric,
        "data_source": data_source,
        "time_range": time_range,
        "algorithm": algorithm,
        "status": "normal",
        "total_points": 120,
        "anomaly_count": 0,
        "mean": 0.045,
        "std": 0.012,
    }
)
```

---

### Q4: 日志级别选择策略

**Question**: 不同情况下应该使用什么日志级别（DEBUG/INFO/WARNING/ERROR）？

**Decision**: 
- **INFO**: 检测开始、检测完成（无论结果如何）、正常执行流程的关键步骤
- **DEBUG**: 详细的算法执行细节（基线计算、阈值比较、异常评分等）、数据统计信息
- **WARNING**: 数据不足、查询返回空结果、非致命性错误
- **ERROR**: 数据源连接失败、查询超时、数据解析错误、致命性错误

**Rationale**:
- 规范要求"正常"结果也要记录 INFO 级别日志，确保所有检测活动都可见
- DEBUG 级别用于深度调试，不会影响生产环境的日志量
- WARNING 用于非致命性问题，不影响功能但需要关注
- ERROR 用于需要立即关注的严重问题

**Alternatives Considered**:
- 所有日志都使用 INFO：会导致日志量过大，不利于区分重要程度
- 正常结果使用 DEBUG：不符合规范要求，用户需要看到所有检测活动

---

### Q5: 性能优化策略

**Question**: 如何确保日志记录不会显著影响异常检测的执行性能？

**Decision**: 
1. 使用异步日志处理（Python logging 默认是异步的）
2. 避免在日志记录中进行复杂计算（预先计算好所有值）
3. 对于大量数据的字段（如异常点列表），只记录摘要信息（如前 10 个异常点）
4. 使用日志级别控制详细程度（生产环境可以设置为 INFO，减少 DEBUG 日志）

**Rationale**:
- Python logging 模块默认使用异步处理，不会阻塞主线程
- 预先计算值可以避免在日志记录时进行耗时操作
- 限制日志数据量可以避免日志系统过载
- 通过日志级别控制可以平衡可见性和性能

**Alternatives Considered**:
- 使用单独的日志线程：Python logging 已经异步，无需额外线程
- 完全禁用详细日志：不符合规范要求，用户需要完整的日志信息

**Implementation Notes**:
- 在检测开始和结束时记录时间戳，可以用于性能分析
- 对于异常点列表，只记录前 10 个异常点的详细信息，避免日志过大
- 使用 `logger.isEnabledFor(logging.DEBUG)` 检查日志级别，避免不必要的字符串格式化

---

### Q6: 日志错误处理

**Question**: 当日志写入失败时，如何处理以确保不影响异常检测功能？

**Decision**: 
- Python logging 模块默认会捕获日志写入异常，不会影响主程序执行
- 如果需要在日志写入失败时记录警告，可以使用 try-except 包裹日志调用（通常不需要）
- 确保日志记录代码不会抛出异常，所有异常都应该被捕获

**Rationale**:
- Python logging 模块设计为"尽力而为"，写入失败不会影响主程序
- 规范要求"日志写入失败不应导致检测功能失败"
- 过度处理日志错误会增加代码复杂度，不符合"简单优先"原则

**Alternatives Considered**:
- 使用自定义日志处理器捕获异常：Python logging 已经处理，无需额外代码
- 完全忽略日志错误：Python logging 默认行为已经足够

---

## Summary

所有技术选择已确定：

1. **日志格式**: 使用 Python 标准 logging 模块，通过 `extra` 参数添加结构化字段，支持 JSON 格式（生产环境）和开发终端格式（本地开发）
2. **上下文信息**: 通过 `ContextManager` 和线程上下文获取工作流 ID、步骤 ID、租户 ID 等信息
3. **结构化字段**: 包含必需字段（用于过滤）、检测参数字段（用于理解上下文）、执行结果字段（用于分析结果）
4. **日志级别**: INFO 用于正常流程和结果，DEBUG 用于详细调试，WARNING 用于非致命问题，ERROR 用于严重错误
5. **性能优化**: 利用 Python logging 的异步特性，预先计算值，限制日志数据量，通过日志级别控制详细程度
6. **错误处理**: 依赖 Python logging 模块的默认错误处理机制，确保日志写入失败不影响主程序

所有决策均基于 Keep 项目的现有架构和最佳实践，最小化代码变更和性能影响。

