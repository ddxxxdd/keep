# Implementation Plan: Anomaly Detector as Custom Provider

**Branch**: `001-anomaly-detector-provider` | **Date**: 2025-01-27 | **Updated**: 2025-01-27 | **Spec**: [spec.md](./spec.md)
**Input**: Feature specification from `/specs/001-anomaly-detector-provider/spec.md`

## Summary

将原本通过 `docker-compose-with-otel.yaml` 中独立后台服务实现的异常检测功能，迁移为 Keep 平台的自定义 Provider。新 Provider 需要同时支持 Prometheus 指标、Tempo 链路追踪和 Loki 日志三类数据源的异常检测，保持与旧服务相同的检测行为和敏感度，并允许在工作流中按需调用。

**新增功能 1**：Provider 在检测到异常时能够自动向 Keep 平台发送告警，完全模拟旧独立服务的自动告警行为，无需通过工作流即可触发告警。  
**新增功能 2**：Provider 支持一种“批量检测模式”，在该模式下用户无需在工作流中逐条写出具体 Prometheus 指标名，而是通过 Provider 配置中的包含/排除规则（如 `include_metrics`/`exclude_metrics`，支持前缀或正则）以及全局最大指标数量上限，从 Prometheus 自动发现一组候选指标集合，仅对这组候选指标执行异常检测，并对每个检测到异常的指标分别自动发送告警。

技术方案：
1. 扩展现有的 `AnomalyDetectorProvider`，添加 Tempo 和 Loki 客户端支持，统一异常检测算法接口，使 Provider 能够根据查询参数自动选择数据源并执行检测。
2. 在 `_query` 方法中集成自动告警发送逻辑：当检测到异常数量超过配置的阈值时，自动调用 `process_event` 函数向 Keep 平台发送告警。
3. 在 `_query` 方法中新增“批量检测模式”分支：当调用方传入特殊的 `metric` 值（例如 `"__all__"`）时，读取 `AnomalyDetectorConfig.include_metrics`/`exclude_metrics` 以及最大指标数量上限，先通过 Prometheus API 自动发现候选指标列表，然后对列表中的每个指标复用“单指标检测 + 自动告警”的完整流程，并返回一份批量检测的汇总结果（本次检测的指标数量、已发送告警数量及每个指标的检测摘要）。
4. 告警包含完整的异常检测信息，并使用 Keep 平台的去重机制（基于 fingerprint）避免重复告警。

## Technical Context

**Language/Version**: Python 3.11+  
**Primary Dependencies**: 
- `numpy`: 数值计算和数组操作
- `scikit-learn`: Isolation Forest 异常检测算法（可选，仅当 algorithm="isolation_forest" 或 "both" 时）
- `requests`: HTTP 客户端，用于 Prometheus/Tempo/Loki API 调用
- `pydantic`: 配置验证和序列化
- `hashlib`: 用于生成告警 fingerprint（SHA-256）
- `uuid`: 用于生成告警 ID
- `datetime`: 用于时间戳处理
- Keep 内部模块：
  - `keep.providers.base.base_provider`: Provider 基类
  - `keep.anomaly_detector.algorithms`: 异常检测算法
  - `keep.anomaly_detector.config`: 配置类
  - `keep.api.tasks.process_event_task.process_event`: 告警处理函数
  - `keep.api.models.alert.AlertDto`: 告警数据模型
  - `keep.api.models.alert.AlertStatus`: 告警状态枚举
  - `keep.api.models.alert.AlertSeverity`: 告警严重程度枚举
  - `keep.contextmanager.contextmanager.ContextManager`: 上下文管理器（获取 tenant_id, workflow_id 等）

**Storage**: N/A（Provider 为无状态查询服务，不持久化数据）  
**Testing**: pytest（单元测试 + 集成测试）  
**Target Platform**: Linux 服务器（Docker 容器环境）  
**Project Type**: Web application backend（Keep 平台的一部分）  
**Performance Goals**: 
- 单次异常检测查询响应时间 < 5 秒（包含 Prometheus/Tempo/Loki API 调用）
- 支持并发查询（通过 Keep 工作流引擎的并发执行机制）

**Constraints**: 
- 必须与现有 Keep Provider 架构兼容
- 必须复用现有的异常检测算法实现（`keep.anomaly_detector.algorithms`）
- 必须保持与旧服务配置参数的兼容性（通过 UI 配置映射到环境变量语义）
- 自动告警发送失败不应影响异常检测结果的正常返回（检测功能优先于告警发送）
- 必须使用 Keep 平台的 `process_event` 函数发送告警，确保告警正确处理和去重

**Scale/Scope**: 
- 单个 Provider 实例可处理多个工作流的并发查询
- 单指标模式下，每个查询可检测单个指标/查询的时间序列数据（通常 100-1000 个数据点）
- 批量检测模式下，每次调用 `_query` 时可自动检测一组候选指标（例如最多 100 条，由配置中的最大指标数量上限控制），避免对 Prometheus 进行全库扫描带来的性能和稳定性风险
- 支持在 Keep UI 中安装和配置多个 Anomaly Detector provider 实例（每个实例可连接不同的 Prometheus/Tempo/Loki）

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

### Pre-Phase 0 Check

- ✅ **Library-First**: Provider 实现遵循 Keep Provider 架构，继承 `BaseProvider`，自包含且可独立测试
- ✅ **CLI Interface**: N/A（Provider 通过 Keep API 和工作流引擎调用，不直接暴露 CLI）
- ✅ **Test-First**: 需要为新功能（Tempo/Loki 支持）编写测试，验证与 Prometheus 路径的一致性
- ✅ **Integration Testing**: 需要测试 Provider 与 Prometheus/Tempo/Loki 的实际 API 集成
- ✅ **Observability**: Provider 使用 Keep 的日志系统，记录查询参数、API 调用、检测结果和告警发送状态
- ✅ **自动告警发送**: Provider 在检测到异常时自动发送告警，使用 Keep 平台的 `process_event` 函数，确保告警正确处理和去重

### Post-Phase 1 Check

*将在 Phase 1 完成后重新评估*

## Project Structure

### Documentation (this feature)

```text
specs/001-anomaly-detector-provider/
├── plan.md              # This file (/speckit.plan command output)
├── research.md          # Phase 0 output (/speckit.plan command)
├── data-model.md        # Phase 1 output (/speckit.plan command)
├── quickstart.md        # Phase 1 output (/speckit.plan command)
├── contracts/           # Phase 1 output (/speckit.plan command)
│   └── provider-api.md  # Provider query method contract
└── tasks.md             # Phase 2 output (/speckit.tasks command - NOT created by /speckit.plan)
```

### Source Code (repository root)

```text
keep/
├── providers/
│   └── anomaly_detector_provider/
│       ├── __init__.py
│       ├── anomaly_detector_provider.py  # 主 Provider 实现（需扩展：添加自动告警发送逻辑）
│       └── clients/                      # 新增：数据源客户端模块
│           ├── __init__.py
│           ├── prometheus_client.py     # Prometheus 客户端（从主文件提取）
│           ├── tempo_client.py           # Tempo 客户端（新增）
│           └── loki_client.py           # Loki 客户端（新增）
├── anomaly_detector/
│   ├── __init__.py
│   ├── algorithms.py                     # 异常检测算法（已存在，复用）
│   └── config.py                        # 配置类（已存在，需扩展）
└── tests/
    └── providers/
        └── anomaly_detector_provider/
            ├── test_prometheus_detection.py
            ├── test_tempo_detection.py   # 新增
            ├── test_loki_detection.py    # 新增
            ├── test_multi_source.py      # 新增：多数据源集成测试
            ├── test_auto_alert_sending.py  # 新增：自动告警发送功能测试
            └── test_alert_deduplication.py # 新增：告警去重测试
```

**Structure Decision**: 
- 采用模块化设计，将 Prometheus/Tempo/Loki 客户端分离到独立模块，便于测试和维护
- 保持与现有 Keep Provider 架构的一致性（Provider 类位于 `keep/providers/{provider_type}_provider/`）
- 复用现有的异常检测算法和配置类，避免重复实现

## Complexity Tracking

> **Fill ONLY if Constitution Check has violations that must be justified**

| Violation | Why Needed | Simpler Alternative Rejected Because |
|-----------|------------|-------------------------------------|
| 新增 `clients/` 子模块 | 需要支持三种不同的数据源 API（Prometheus/Tempo/Loki），每种 API 有不同的查询方式和数据格式 | 将所有客户端逻辑放在主 Provider 文件中会导致代码过长（>1000 行），难以维护和测试 |
| 扩展 `AnomalyDetectorConfig` 支持 Tempo/Loki | 需要统一的配置接口来管理多数据源连接信息 | 为每个数据源创建独立的 Provider 会破坏"统一异常检测能力"的设计目标，增加用户配置复杂度 |
| 在 `_query` 方法中直接调用 `process_event` | 需要自动发送告警，完全模拟旧服务的自动告警行为 | 通过工作流发送告警会增加用户配置复杂度，不符合"与旧服务效果一样"的需求 |
| 添加 `min_anomaly_count_for_alert` 配置参数 | 需要支持可配置的异常数量阈值，控制告警发送频率 | 固定阈值无法满足不同场景的需求，可配置性提供更好的灵活性 |
