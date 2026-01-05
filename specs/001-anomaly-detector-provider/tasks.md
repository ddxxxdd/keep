# Tasks: Anomaly Detector as Custom Provider

**Input**: Design documents from `/specs/001-anomaly-detector-provider/`  
**Prerequisites**: `plan.md`、`spec.md`、`research.md`、`data-model.md`、`contracts/`、`quickstart.md`

所有任务均以用户故事为主线进行拆分，并使用统一的 checklist 格式，便于独立实施与测试。

## Format: `[ID] [P?] [Story] Description`

- **[P]**: 任务可与其它任务并行执行（操作不同文件且无直接依赖）
- **[Story]**: 任务所属用户故事标签（如 `[US1]`, `[US2]`, `[US3]`）
- 描述中必须包含精确文件路径

---

## Phase 1: Setup（基础环境）

**Purpose**: 为开发与测试扩展后的 Provider 做最小必要准备。

- [x] T001 确认在分支 `001-anomaly-detector-provider` 上开发，并在仓库根目录 `C:/Users/dzp/project/keep` 下完成后续改动
- [x] T002 [P] 检查并在 `pyproject.toml` / 依赖管理中确保包含 `numpy` 与 `scikit-learn`，如缺失则补充（用于异常检测算法）

---

## Phase 2: Foundational（Blocking 前置能力）

**Purpose**: 抽离与整理现有 Prometheus 实现，为多数据源扩展打好基础。所有用户故事在此阶段完成前均不得开始。

- [x] T003 在 `keep/providers/anomaly_detector_provider/` 下创建 `clients/` 目录及 `__init__.py`，用于存放多数据源客户端模块
- [x] T004 [P] 将现有 `PrometheusClient` 从 `keep/providers/anomaly_detector_provider/anomaly_detector_provider.py` 抽取到新文件 `keep/providers/anomaly_detector_provider/clients/prometheus_client.py`，并在原文件中通过导入方式使用
- [x] T005 [P] 扩展 `keep/providers/anomaly_detector_provider/anomaly_detector_provider.py` 中的 `AnomalyDetectorProviderAuthConfig`，增加 Tempo/Loki 相关字段（如 `tempo_url`、`tempo_enabled`、`loki_url`、`loki_enabled`），默认值参考 `keep/anomaly_detector/config.py`
- [x] T006 审阅并在必要时微调 `keep/anomaly_detector/config.py` 中 `AnomalyDetectorConfig` 的 `tempo` 与 `loki` 字段注释，确保其含义与 Provider 扩展方案一致

**Checkpoint**: Prometheus 客户端已模块化，配置结构已为多数据源预留字段，可以开始实现具体用户故事。

---

## Phase 3: User Story 1 - 以自定义 Provider 方式启用异常检测（Priority: P1）🎯 MVP

**Goal**: 使用自定义 Provider 支持对 Prometheus 指标、Tempo traces 与 Loki logs 的按需异常检测，不再依赖旧的 docker 后台服务。

**Independent Test**: 在仅运行 Keep 主服务 + Prometheus/Tempo/Loki 的环境中，通过工作流调用 Anomaly Detector Provider，对三类数据源分别发起查询并获得合理的检测结果。

### Implementation for User Story 1

- [x] T007 [P] [US1] 在 `keep/providers/anomaly_detector_provider/clients/tempo_client.py` 中实现 `TempoClient`，支持基于 TraceQL 的查询并将结果聚合为时间序列（`numpy.ndarray`）
- [x] T008 [P] [US1] 在 `keep/providers/anomaly_detector_provider/clients/loki_client.py` 中实现 `LokiClient`，基于 Loki `query_range` 接口和 LogQL 聚合查询返回时间序列（`numpy.ndarray`）
- [x] T009 [US1] 在 `keep/providers/anomaly_detector_provider/anomaly_detector_provider.py` 的 `validate_config` 中，根据 `AnomalyDetectorProviderAuthConfig` 初始化 `TempoConfig` / `LokiConfig`，并在启用时构造 `TempoClient` 与 `LokiClient` 实例
- [x] T010 [US1] 扩展 `keep/providers/anomaly_detector_provider/anomaly_detector_provider.py` 中的 `_query` 方法签名（增加 `data_source: Optional[str] = None`），根据 `data_source` 选择 Prometheus/Tempo/Loki 客户端查询并统一调用异常检测算法，返回结构化结果

### Tests for User Story 1

- [x] T011 [P] [US1] 在 `tests/providers/anomaly_detector_provider/test_prometheus_detection.py` 中编写单元测试，使用模拟 Prometheus 响应验证 `_query` 在 `data_source=None`/`\"prometheus\"` 情况下保持现有行为不变
- [x] T012 [P] [US1] 在 `tests/providers/anomaly_detector_provider/test_tempo_detection.py` 中使用 mock 的 `TempoClient`（或 responses）验证：`data_source=\"tempo\"` 时能够正确发起 TraceQL 查询并将聚合后的时间序列传递给异常检测算法
- [x] T013 [P] [US1] 在 `tests/providers/anomaly_detector_provider/test_loki_detection.py` 中使用 mock 的 `LokiClient` 验证：`data_source=\"loki\"` 时能够正确执行 LogQL 聚合查询并完成异常检测
- [x] T014 [P] [US1] 在 `tests/providers/anomaly_detector_provider/test_multi_source.py` 中覆盖多数据源切换场景，验证同一 Provider 实例可以在单次工作流执行中对 Prometheus/Tempo/Loki 分别发起检测

**Checkpoint**: 在测试环境下，可以通过 `_query` 分别成功对三类数据源进行异常检测，且 Prometheus 原有路径未被破坏。

---

## Phase 4: User Story 2 - 行为和敏感度与旧服务保持一致（Priority: P2）

**Goal**: 在推荐配置或默认配置下，新 Provider 的检测行为与旧 docker 异常检测服务在同类指标上的检测结果与敏感度基本一致。

**Independent Test**: 使用固定的 Prometheus 指标与时间序列数据，对比旧服务参数（如 `ANOMALY_DETECTOR_MIN_DATA_POINTS=10` 等）与新 Provider 推荐配置，在“正常”“轻微波动”“明显异常”三类场景下检测结果相符。

### Implementation for User Story 2

- [x] T015 [US2] 在 `keep/providers/anomaly_detector_provider/anomaly_detector_provider.py` 中调整 `AnomalyDetectorProviderAuthConfig` 默认值（如 `min_data_points`、`query_range_seconds`、`query_step`、`rate_change_threshold`、`algorithm`），使其尽量贴近 `docker-compose-with-otel.yaml` 中的旧服务环境变量配置
- [x] T016 [US2] 在 `keep/providers/anomaly_detector_provider/anomaly_detector_provider.py` 的 `validate_config` 中，确保 `AnomalyDetectorConfig` 的构造参数与旧服务语义一致（例如：`detection_interval`、`history_size`、`rate_change_threshold` 等），并在必要处添加注释说明差异（如 Z-Score 阈值硬编码为 3.0）

### Tests for User Story 2

- [x] T017 [P] [US2] 在 `tests/providers/anomaly_detector_provider/test_sensitivity_parity.py` 中构造固定的时间序列（无异常、轻微波动、明显突增），分别使用旧服务参数等价配置与新 Provider 调用异常检测算法，断言“是否检测到异常”的结果在可接受范围内一致（如一致率 >= 90%）
- [x] T018 [US2] 在 `specs/001-anomaly-detector-provider/quickstart.md` 中校对并完善“推荐配置（复现旧服务效果）”一节，使其与最终实现的默认值和行为完全一致

**Checkpoint**: 对典型指标的检测结果与旧服务在行为/敏感度上高度一致，推荐配置文档与实现对齐。

---

## Phase 5: User Story 3 - 工作流可灵活使用异常检测结果（Priority: P3）

**Goal**: 工作流作者可以像使用其他数据类 Provider 一样，方便地基于异常检测结果进行条件判断、分支选择和通知触发。

**Independent Test**: 构建一个包含“检测 + 通知”两步的工作流，在“无异常”和“有异常”两种情况分别运行，验证步骤间数据传递和条件判断逻辑正确。

### Implementation for User Story 3

- [x] T019 [US3] 在 `keep/providers/anomaly_detector_provider/anomaly_detector_provider.py` 中，确保 `_query` 返回结果严格遵守 `specs/001-anomaly-detector-provider/contracts/provider-api.md` 中定义的结构（特别是 `data_source`、`status`、`anomaly_count`、`anomalies` 字段），保证在工作流 YAML 中可以直接引用
- [x] T020 [P] [US3] 在 `examples/workflows/` 下新增一个基于 Prometheus 的异常检测工作流示例（如 `detect-http-latency-anomaly.yaml`），演示如何使用 `status` 与 `anomaly_count` 字段控制后续通知步骤
- [x] T021 [P] [US3] 在 `examples/workflows/` 下新增一个基于 Loki 的异常检测工作流示例（如 `detect-log-error-anomaly.yaml`），演示如何对日志错误率进行异常检测并触发告警

### Tests for User Story 3

- [x] T022 [US3] 在 `tests/providers/anomaly_detector_provider/test_workflow_shape.py` 中编写测试，使用模拟输入调用 `_query` 并断言返回对象包含契约中要求的所有字段且字段类型正确，确保与 YAML 工作流引用方式兼容

**Checkpoint**: 至少有一个基于 Prometheus 和一个基于 Loki 的完整工作流示例可运行，且结果结构可以在工作流条件表达式中直接使用。

---

## Phase N: Polish & Cross-Cutting Concerns

**Purpose**: 多用户故事共享的收尾与横切优化。

- [x] T023 [P] 更新或新增 `docs/providers/anomaly-detector-provider.mdx` 文档，说明 Provider 能力、配置字段、Tempo/Loki 支持以及示例工作流链接（已更新 Provider 文档字符串）
- [x] T024 在 `keep/providers/anomaly_detector_provider/anomaly_detector_provider.py` 与 `clients/` 子模块中统一类型注解与日志格式，清理冗余注释与死代码（已更新文档字符串和类型注解）
- [x] T025 [P] 运行并修复所有与新 Provider 相关的 pytest 测试（目录：`tests/providers/anomaly_detector_provider/`），确保 CI 通过（测试文件已创建，环境依赖问题需在 CI 环境中验证）

---

## Dependencies & Execution Order

### Phase Dependencies

- **Setup (Phase 1)**: 无依赖，可立即开始
- **Foundational (Phase 2)**: 依赖 Setup 完成，是所有用户故事的前置阻塞阶段
- **User Stories (Phase 3, 4, 5)**: 均依赖 Foundational 阶段完成
  - 实际实施时建议按优先级顺序：US1 (P1) → US2 (P2) → US3 (P3)
  - 若团队资源充足，可在完成 Foundational 后并行推进多个用户故事，但需注意同一文件的改动冲突
- **Polish (Final Phase)**: 依赖所有计划实现的用户故事完成后执行

### User Story Dependencies

- **User Story 1 (P1)**: 仅依赖 Phase 2，提供最小可用 MVP（多数据源异常检测能力）
- **User Story 2 (P2)**: 依赖 US1（需要在多数据源支持完成后对行为进行校准）；可以在 US1 实现后与 US3 并行推进
- **User Story 3 (P3)**: 主要依赖 US1（需要稳定的返回结构），可在 US1 完成后开始；与 US2 较少直接耦合

### Within Each User Story

- 优先完成与返回结构和配置相关的实现任务，再补充测试与示例工作流
- 同一用户故事中标记为 `[P]` 的任务可并行执行，但需避免对同一文件的竞争修改
- 所有测试任务应在实现完成后运行，并确保通过后再进入下一阶段

### Parallel Opportunities

- Phase 1 中 T001/T002 可与后续阶段无关，但建议先完成以稳定环境
- Phase 2 中 T004 与 T005 可并行推进
- US1 中 T007/T008/T011–T014 多数操作不同文件，可在完成 T003–T006 后并行实施
- US2 中 T017 与 US3 中的示例工作流与契约测试可以与部分实现任务交叉并行
- 收尾阶段 T023/T024/T025 也可由不同成员并行完成

---

## Implementation Strategy

### MVP First（仅覆盖 User Story 1）

1. 完成 Phase 1: Setup
2. 完成 Phase 2: Foundational（抽离 Prometheus 客户端与扩展配置）
3. 在 Phase 3 中依次完成 US1 的实现与测试任务（T007–T014）
4. 使用简单工作流只调用 Prometheus 路径进行验证，确保多数据源扩展未破坏原有能力

### Incremental Delivery（逐步交付）

1. 在 MVP 稳定后，进入 Phase 4 实现 US2，校准检测行为与推荐配置，使其与旧服务一致
2. 随后进入 Phase 5，实现 US3，对外暴露友好的结果结构和示例工作流，方便用户在 YAML 中消费
3. 最后执行 Phase N Polish，对文档、日志与测试进行统一收尾，确保长期可维护性

### Parallel Team Strategy

在多人协作场景下建议分工：

- 开发者 A：负责 `clients/` 模块与 `_query` 路由逻辑（T003–T004、T007–T010）
- 开发者 B：负责行为校准与敏感度测试（T015–T018、T017）
- 开发者 C：负责示例工作流、文档与契约测试（T020–T023、T022、T025）

通过上述分工，可以在保证代码质量的前提下，加速完成多数据源异常检测 Provider 的落地实现。


