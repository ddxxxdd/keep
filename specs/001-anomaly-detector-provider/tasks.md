# 任务列表: Anomaly Detector Provider 扩展

**功能分支**: `001-anomaly-detector-provider`  
**创建日期**: 2025-01-27  
**更新日期**: 2025-01-27  
**规范文档**: [spec.md](./spec.md) | [plan.md](./plan.md)

## 概述

本任务列表基于功能规范中的用户故事，按优先级组织实现任务。每个用户故事都是独立可测试的增量，可以单独实现和验证。

## 任务统计

- **总任务数**: 55
- **Phase 1 - 基础设置**: 5 个任务
- **Phase 2 - 用户故事 1 (P1)**: 18 个任务
- **Phase 3 - 用户故事 4 (P1)**: 12 个任务
- **Phase 4 - 用户故事 2 (P2)**: 10 个任务
- **Phase 5 - 用户故事 3 (P3)**: 6 个任务
- **Phase 6 - 收尾工作**: 4 个任务

## 用户故事依赖关系

```
[Phase 1: 基础设置] 
    ↓
[Phase 2: 用户故事 1 - 以自定义 Provider 方式启用异常检测] (P1)
    ↓
[Phase 3: 用户故事 4 - 检测到异常后自动发送告警到 Keep 平台] (P1) - 依赖用户故事 1
    ↓
[Phase 4: 用户故事 2 - 行为和敏感度与旧服务保持一致] (P2) - 依赖用户故事 1
    ↓
[Phase 5: 用户故事 3 - 工作流可灵活使用异常检测结果] (P3) - 依赖用户故事 1
    ↓
[Phase 6: 收尾工作]
```

## 并行执行机会

- **Phase 4 和 Phase 5** 可以并行执行（都依赖 Phase 2，但彼此独立）
- **测试任务** 可以在实现任务完成后并行执行
- **文档任务** 可以在实现完成后并行执行
- **客户端实现任务**（Prometheus/Tempo/Loki）可以并行执行

## 实现策略

### MVP 范围（最小可行产品）

MVP 包括：
- **Phase 2 (用户故事 1)**: 基本的异常检测 Provider 功能（支持 Prometheus）
- **Phase 3 (用户故事 4)**: 自动告警发送功能

这两个阶段提供了核心价值：能够检测异常并自动发送告警，完全替代旧服务。

### 增量交付

1. **第一阶段**: Phase 2 + Phase 3（MVP）
2. **第二阶段**: Phase 4（确保行为一致性）
3. **第三阶段**: Phase 5（工作流集成优化）

---

## Phase 1: 基础设置

**目标**: 准备项目结构和开发环境

**独立测试标准**: 
- 项目结构符合计划文档中的定义
- 所有必要的目录和文件已创建
- 开发环境可以正常运行测试

### 任务列表

- [X] T001 创建 `keep/providers/anomaly_detector_provider/clients/` 目录结构
- [X] T002 创建 `keep/providers/anomaly_detector_provider/clients/__init__.py` 文件
- [X] T003 [P] 创建 `keep/providers/anomaly_detector_provider/clients/prometheus_client.py` 文件框架
- [X] T004 [P] 创建 `keep/providers/anomaly_detector_provider/clients/tempo_client.py` 文件框架
- [X] T005 [P] 创建 `keep/providers/anomaly_detector_provider/clients/loki_client.py` 文件框架

---

## Phase 2: 用户故事 1 - 以自定义 Provider 方式启用异常检测 (P1)

**目标**: 实现基本的异常检测 Provider，支持 Prometheus 指标检测，可以在工作流中使用。

**独立测试标准**: 在本地或测试环境中仅启用 Keep 主服务和 Prometheus，不启动旧的异常检测容器；在 Keep UI 中安装并配置 Anomaly Detector provider，执行包含该 provider 的测试工作流，确认可以正常对指定 Prometheus 指标返回异常检测结果。

### 任务列表

- [X] T006 [US1] 在 `AnomalyDetectorProviderAuthConfig` 中添加 `min_anomaly_count_for_alert` 配置字段（默认值为 1）到 `keep/providers/anomaly_detector_provider/anomaly_detector_provider.py`
- [X] T007 [US1] 从 `anomaly_detector_provider.py` 中提取 Prometheus 客户端逻辑到 `keep/providers/anomaly_detector_provider/clients/prometheus_client.py`
- [X] T008 [US1] 实现 `PrometheusClient.query_range()` 方法，支持 Prometheus API 查询到 `keep/providers/anomaly_detector_provider/clients/prometheus_client.py`
- [X] T009 [US1] 实现 `PrometheusClient` 的认证支持（基本认证、SSL 验证）到 `keep/providers/anomaly_detector_provider/clients/prometheus_client.py`
- [X] T010 [US1] 在 `AnomalyDetectorProvider._query()` 方法中集成 Prometheus 客户端调用到 `keep/providers/anomaly_detector_provider/anomaly_detector_provider.py`
- [X] T011 [US1] 实现自动识别计数型指标并包装 `rate()` 的逻辑到 `keep/providers/anomaly_detector_provider/anomaly_detector_provider.py`
- [X] T012 [US1] 实现异常检测结果格式化，返回包含 `status`, `anomaly_count`, `anomalies`, `total_points`, `mean`, `std` 等字段的字典到 `keep/providers/anomaly_detector_provider/anomaly_detector_provider.py`
- [X] T013 [US1] 添加错误处理：当 Prometheus 连接失败时返回清晰的错误信息到 `keep/providers/anomaly_detector_provider/anomaly_detector_provider.py`
- [X] T014 [US1] 添加错误处理：当查询无数据时返回 `status="no_data"` 到 `keep/providers/anomaly_detector_provider/anomaly_detector_provider.py`
- [X] T015 [US1] 添加错误处理：当数据点不足时返回 `status="insufficient_data"` 并包含 `data_points` 和 `min_data_points` 字段到 `keep/providers/anomaly_detector_provider/anomaly_detector_provider.py`
- [X] T016 [US1] 实现 `time_range` 参数解析（支持 `"30m"`, `"1h"`, `"2d"` 等格式）到 `keep/providers/anomaly_detector_provider/anomaly_detector_provider.py`
- [X] T017 [US1] 添加日志记录：记录查询参数、API 调用、检测结果到 `keep/providers/anomaly_detector_provider/anomaly_detector_provider.py`
- [X] T018 [US1] [P] 创建单元测试 `test_prometheus_detection.py` 验证 Prometheus 查询和异常检测功能到 `tests/providers/anomaly_detector_provider/test_prometheus_detection.py`
- [ ] T019 [US1] [P] 创建集成测试验证 Provider 在工作流中的使用到 `tests/providers/anomaly_detector_provider/test_workflow_integration.py`
- [X] T020 [US1] [P] 更新 Provider 文档说明如何在 UI 中配置和使用到 `specs/001-anomaly-detector-provider/quickstart.md`
- [ ] T053 [US1] 在 `AnomalyDetectorProviderAuthConfig` 中增加 `include_metrics`/`exclude_metrics` 以及“最大批量检测指标数量”配置字段，并在构造 `AnomalyDetectorConfig` 时传递这些字段到 `keep/providers/anomaly_detector_provider/anomaly_detector_provider.py`
- [ ] T054 [US1] 在 `AnomalyDetectorProvider._query()` 中增加“批量检测模式”逻辑（例如当 `metric="__all__"` 时），按 `include_metrics`/`exclude_metrics` 和数量上限从 Prometheus 自动发现候选指标列表，对每个候选指标依次复用现有单指标检测 + 自动告警流程，并返回包含批量检测汇总信息的结果到 `keep/providers/anomaly_detector_provider/anomaly_detector_provider.py`
- [ ] T055 [US1] [P] 创建单元测试覆盖批量检测模式（包含/排除规则、生效的最大指标数量上限以及汇总结果结构）到 `tests/providers/anomaly_detector_provider/test_batch_mode.py`

---

## Phase 3: 用户故事 4 - 检测到异常后自动发送告警到 Keep 平台 (P1)

**目标**: 在检测到异常时自动向 Keep 平台发送告警，完全模拟旧独立服务的自动告警行为。

**独立测试标准**: 配置 Anomaly Detector provider 并执行异常检测，当检测到异常数量超过配置的阈值时，在 Keep 平台的告警列表中能够看到自动创建的告警，且告警包含完整的异常检测信息。

### 任务列表

- [X] T021 [US4] 导入必要的模块：`process_event`, `AlertDto`, `AlertStatus`, `AlertSeverity` 到 `keep/providers/anomaly_detector_provider/anomaly_detector_provider.py`
- [X] T022 [US4] 实现 `_generate_fingerprint()` 方法，基于指标名称和关键标签生成告警 fingerprint 到 `keep/providers/anomaly_detector_provider/anomaly_detector_provider.py`
- [X] T023 [US4] 实现 `_build_alert_dto()` 方法，构建包含完整异常检测信息的 `AlertDto` 对象到 `keep/providers/anomaly_detector_provider/anomaly_detector_provider.py`
- [X] T024 [US4] 在 `_query()` 方法中添加异常数量阈值检查逻辑（`anomaly_count >= min_anomaly_count_for_alert`）到 `keep/providers/anomaly_detector_provider/anomaly_detector_provider.py`
- [X] T025 [US4] 在 `_query()` 方法中集成自动告警发送逻辑，当满足阈值条件时调用 `process_event()` 到 `keep/providers/anomaly_detector_provider/anomaly_detector_provider.py`
- [X] T026 [US4] 添加告警发送失败的错误处理，记录错误日志但不影响检测结果返回到 `keep/providers/anomaly_detector_provider/anomaly_detector_provider.py`
- [X] T027 [US4] 确保告警包含完整信息：告警名称（基于指标名称）、告警描述（包含异常数量、检测算法、统计信息等）、告警严重程度、异常点详情（时间戳、数值、评分）、检测时间等到 `keep/providers/anomaly_detector_provider/anomaly_detector_provider.py`
- [X] T028 [US4] 确保告警使用正确的 fingerprint 以便 Keep 平台进行去重到 `keep/providers/anomaly_detector_provider/anomaly_detector_provider.py`
- [X] T029 [US4] 添加告警发送状态的日志记录到 `keep/providers/anomaly_detector_provider/anomaly_detector_provider.py`
- [ ] T030 [US4] [P] 创建单元测试 `test_auto_alert_sending.py` 验证自动告警发送功能到 `tests/providers/anomaly_detector_provider/test_auto_alert_sending.py`
- [ ] T031 [US4] [P] 创建测试验证告警去重机制（基于 fingerprint）到 `tests/providers/anomaly_detector_provider/test_alert_deduplication.py`
- [ ] T032 [US4] [P] 创建集成测试验证告警在 Keep 前端界面中可见到 `tests/providers/anomaly_detector_provider/test_alert_integration.py`

---

## Phase 4: 用户故事 2 - 行为和敏感度与旧服务保持一致 (P2)

**目标**: 确保新 Provider 的检测行为和敏感度与旧服务基本一致，避免迁移后出现大量漏报或误报。

**独立测试标准**: 对同一条 Prometheus 指标，在相同时间窗口内，对比旧服务配置与新 provider 推荐配置，在典型"正常"、"轻微波动"和"明显异常"三种场景下的输出差异，确认阈值和行为在可接受范围内。

### 任务列表

- [ ] T033 [US2] 调整 `AnomalyDetectorProviderAuthConfig` 的默认值，使其与旧服务配置一致（`algorithm="zscore"`, `min_data_points=10`, `query_range_seconds=1800`, `query_step="15s"`, `rate_change_threshold=0.3`）到 `keep/providers/anomaly_detector_provider/anomaly_detector_provider.py`
- [ ] T034 [US2] 验证异常检测算法参数与旧服务的一致性（Z-Score 阈值、环比变化阈值等）到 `keep/providers/anomaly_detector_provider/anomaly_detector_provider.py`
- [ ] T035 [US2] 测试基线为 0 的指标处理逻辑（避免全部被视为异常）到 `keep/providers/anomaly_detector_provider/anomaly_detector_provider.py`
- [ ] T036 [US2] 验证计数型指标的 `rate()` 包装行为与旧服务一致到 `keep/providers/anomaly_detector_provider/anomaly_detector_provider.py`
- [ ] T037 [US2] [P] 创建对比测试，使用相同配置对比新旧实现的检测结果到 `tests/providers/anomaly_detector_provider/test_behavior_consistency.py`
- [ ] T038 [US2] [P] 创建性能测试，确保单次检测响应时间 < 5 秒到 `tests/providers/anomaly_detector_provider/test_performance.py`
- [ ] T039 [US2] [P] 创建推荐配置文档，说明如何在 UI 中设置各个字段以复现旧服务效果到 `specs/001-anomaly-detector-provider/quickstart.md`
- [ ] T040 [US2] [P] 更新文档说明迁移步骤和配置对比到 `specs/001-anomaly-detector-provider/quickstart.md`
- [ ] T041 [US2] 验证异常检测结果的一致性（在相同场景下异常数量偏差不超过 20%）到 `keep/providers/anomaly_detector_provider/anomaly_detector_provider.py`
- [ ] T042 [US2] 添加配置验证逻辑，确保配置参数在合理范围内到 `keep/providers/anomaly_detector_provider/anomaly_detector_provider.py`

---

## Phase 5: 用户故事 3 - 工作流可灵活使用异常检测结果 (P3)

**目标**: 确保在工作流中可以方便地使用异常检测结果进行条件判断、分支选择和通知触发。

**独立测试标准**: 构建一个简单工作流：先调用 Anomaly Detector provider 得到异常检测结果，再根据 `status` 或 `anomaly_count` 是否大于 0 决定是否发送通知；验证在有/无异常的情况下工作流行为符合预期。

### 任务列表

- [ ] T043 [US3] 验证 `_query()` 返回结果格式符合工作流使用要求（包含 `status`, `anomaly_count` 等字段）到 `keep/providers/anomaly_detector_provider/anomaly_detector_provider.py`
- [ ] T044 [US3] 确保异常点列表格式便于在工作流中遍历和处理（按时间索引排序）到 `keep/providers/anomaly_detector_provider/anomaly_detector_provider.py`
- [ ] T045 [US3] [P] 创建示例工作流，展示如何使用异常检测结果进行条件判断到 `examples/workflows/detect-anomaly-and-notify.yaml`
- [ ] T046 [US3] [P] 创建测试验证工作流中基于检测结果的条件分支到 `tests/providers/anomaly_detector_provider/test_workflow_conditional.py`
- [ ] T047 [US3] [P] 创建集成测试验证完整工作流场景（检测 → 条件判断 → 通知）到 `tests/providers/anomaly_detector_provider/test_workflow_complete.py`
- [ ] T048 [US3] [P] 更新文档说明如何在工作流中使用异常检测结果到 `specs/001-anomaly-detector-provider/quickstart.md`

---

## Phase 6: 收尾工作

**目标**: 完善文档、优化代码、确保质量。

### 任务列表

- [ ] T049 更新 Provider 的 `__init__.py` 确保正确导出到 `keep/providers/anomaly_detector_provider/__init__.py`
- [ ] T050 运行所有测试并确保通过率 100% 到 `tests/providers/anomaly_detector_provider/`
- [ ] T051 代码审查：检查错误处理、日志记录、代码风格等到 `keep/providers/anomaly_detector_provider/`
- [ ] T052 更新主 README 或文档，说明新功能的使用方法到项目根目录的文档文件

---

## 任务完成检查清单

在开始实现之前，请确认：
- [ ] 已阅读并理解功能规范 (`spec.md`)
- [ ] 已阅读并理解实现计划 (`plan.md`)
- [ ] 已阅读并理解研究文档 (`research.md`)
- [ ] 已阅读并理解数据模型 (`data-model.md`)
- [ ] 已阅读并理解 API 契约 (`contracts/provider-api.md`)
- [ ] 开发环境已准备就绪（Python 3.11+, pytest, 必要的依赖）

在完成每个用户故事后，请确认：
- [ ] 所有相关任务已完成
- [ ] 独立测试标准已满足
- [ ] 所有测试用例已通过
- [ ] 代码已通过代码审查
- [ ] 文档已更新

---

## 注意事项

1. **任务顺序**: 严格按照依赖关系执行任务，Phase 3 必须在 Phase 2 完成后开始。
2. **测试优先**: 建议采用 TDD 方式，先编写测试再实现功能。
3. **错误处理**: 所有错误情况都必须有清晰的错误信息和日志记录。
4. **代码复用**: 充分利用现有的异常检测算法和 Keep Provider 基础设施。
5. **向后兼容**: 确保新功能不影响现有的 Provider 使用方式。
6. **中文描述**: 所有代码注释、日志消息和文档都应使用中文。

---

## 任务格式说明

每个任务遵循以下格式：
- `- [ ]` - Markdown 复选框
- `T###` - 任务 ID（顺序编号）
- `[P]` - 可选，表示任务可以并行执行
- `[US#]` - 用户故事标签（仅用户故事阶段的任务需要）
- 任务描述 - 清晰说明要做什么
- `到 <文件路径>` - 明确指定目标文件

示例：
- `- [ ] T001 创建目录结构到 keep/providers/anomaly_detector_provider/clients/`
- `- [ ] T010 [US1] 实现查询方法到 keep/providers/anomaly_detector_provider/anomaly_detector_provider.py`
- `- [ ] T018 [US1] [P] 创建测试文件到 tests/providers/anomaly_detector_provider/test_prometheus_detection.py`
