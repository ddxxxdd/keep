# Implementation Plan: Anomaly Detector as Custom Provider

**Branch**: `001-anomaly-detector-provider` | **Date**: 2025-12-29 | **Spec**: [spec.md](./spec.md)
**Input**: Feature specification from `/specs/001-anomaly-detector-provider/spec.md`

## Summary

将原本通过 `docker-compose-with-otel.yaml` 中独立后台服务实现的异常检测功能，迁移为 Keep 平台的自定义 Provider。新 Provider 需要同时支持 Prometheus 指标、Tempo 链路追踪和 Loki 日志三类数据源的异常检测，保持与旧服务相同的检测行为和敏感度，并允许在工作流中按需调用。

技术方案：扩展现有的 `AnomalyDetectorProvider`，添加 Tempo 和 Loki 客户端支持，统一异常检测算法接口，使 Provider 能够根据查询参数自动选择数据源并执行检测。

## Technical Context

**Language/Version**: Python 3.11+  
**Primary Dependencies**: 
- `numpy`: 数值计算和数组操作
- `scikit-learn`: Isolation Forest 异常检测算法（可选，仅当 algorithm="isolation_forest" 或 "both" 时）
- `requests`: HTTP 客户端，用于 Prometheus/Tempo/Loki API 调用
- `pydantic`: 配置验证和序列化
- Keep 内部模块：`keep.providers.base.base_provider`, `keep.anomaly_detector.algorithms`, `keep.anomaly_detector.config`

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

**Scale/Scope**: 
- 单个 Provider 实例可处理多个工作流的并发查询
- 每个查询可检测单个指标/查询的时间序列数据（通常 100-1000 个数据点）
- 支持在 Keep UI 中安装和配置多个 Anomaly Detector provider 实例（每个实例可连接不同的 Prometheus/Tempo/Loki）

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

### Pre-Phase 0 Check

- ✅ **Library-First**: Provider 实现遵循 Keep Provider 架构，继承 `BaseProvider`，自包含且可独立测试
- ✅ **CLI Interface**: N/A（Provider 通过 Keep API 和工作流引擎调用，不直接暴露 CLI）
- ✅ **Test-First**: 需要为新功能（Tempo/Loki 支持）编写测试，验证与 Prometheus 路径的一致性
- ✅ **Integration Testing**: 需要测试 Provider 与 Prometheus/Tempo/Loki 的实际 API 集成
- ✅ **Observability**: Provider 使用 Keep 的日志系统，记录查询参数、API 调用和检测结果

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
│       ├── anomaly_detector_provider.py  # 主 Provider 实现（需扩展）
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
            └── test_multi_source.py      # 新增：多数据源集成测试
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
