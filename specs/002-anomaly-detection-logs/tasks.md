---

description: "Tasks for feature implementation: 异常检测日志记录功能"
---

# Tasks: 异常检测日志记录功能

**Input**: 设计文档来自 `/specs/002-anomaly-detection-logs/`  
**Prerequisites**: `plan.md`（已完成）、`spec.md`（已完成）、`research.md`、`data-model.md`、`contracts/`、`quickstart.md`

**Tests**: 本功能涉及日志可观测性和兼容性，包含必要的测试任务（单元测试与集成测试）。  
**Organization**: 任务按用户故事组织，保证每个故事可以独立实现和测试。

## 格式说明：`[ID] [P?] [Story] 描述（包含文件路径）`

- **[P]**: 该任务可并行执行（不同文件、无前置依赖）
- **[Story]**: 用户故事标签（US1 / US2 / US3）
- 描述中必须包含明确的文件路径

---

## Phase 1: Setup（共享基础环境）

**Purpose**: 确保本地 / 开发环境具备实现和验证异常检测日志功能的基本条件。

- [ ] T001 检查并安装 Python 3.11+ 运行环境（参考 `pyproject.toml`）
- [ ] T002 [P] 确认后端服务已加载 `keep.api.logging.setup_logging` 并使用统一日志配置（文件：`keep/api/logging.py`、后端入口文件）
- [ ] T003 [P] 在本地或开发环境中配置 `LOG_FORMAT` 与 `LOG_LEVEL` 环境变量（推荐：`LOG_FORMAT=open_telemetry`，`LOG_LEVEL=INFO`），确保日志以结构化格式输出
- [ ] T004 配置或确认日志聚合系统（如 Loki/ELK）已接入后端标准输出日志（部署配置与 `docker-compose.yml` / Kubernetes 配置）
- [ ] T005 [P] 为异常检测 Provider 增加或确认测试骨架文件存在：`tests/providers/anomaly_detector_provider/test_anomaly_detector_provider.py`

---

## Phase 2: Foundational（阻塞性前置能力）

**Purpose**: 搭建所有用户故事都依赖的基础能力和公共工具。

**⚠️ CRITICAL**: 在本阶段完成前，禁止开始任何用户故事实现工作。

- [ ] T006 在 `keep/providers/anomaly_detector_provider/anomaly_detector_provider.py` 中审查并整理现有日志相关代码，列出当前已记录的日志点及字段（便于后续对齐规格）
- [ ] T007 [P] 在 `anomaly_detector_provider.py` 中确认或实现 `_get_logging_context` 辅助方法，统一组装 `provider_type`、`workflow_id`、`workflow_execution_id`、`step_id`、`tenant_id` 等上下文字段
- [ ] T008 [P] 确认 `AnomalyDetectionLogEntry` 所需字段与 `_get_logging_context` 和 `_query` 中的 `extra` 字段一一对应（参考：`specs/002-anomaly-detection-logs/data-model.md`）
- [ ] T009 在 `tests/providers/anomaly_detector_provider/test_anomaly_detector_provider.py` 中添加基础测试工具（如使用 `caplog` 或自定义 handler）以捕获并断言 Provider 产生的日志记录

**Checkpoint**: 完成以上任务后，可以开始基于用户故事的实现与测试。

---

## Phase 3: User Story 1 - 查看所有异常检测执行日志 (Priority: P1) 🎯 MVP

**Goal**: 无论异常检测结果如何（正常、异常、无数据、数据不足等），在日志系统中都能看到完整的异常检测执行信息（包含查询参数、数据获取情况、检测算法执行过程、统计结果、检测结果等）。

**Independent Test**: 在工作流中多次调用 Anomaly Detector Provider，分别覆盖正常、有异常、无数据、数据不足等情况；对每次执行，通过日志聚合系统或测试捕获日志，验证都存在完整且结构化的日志记录。

### Tests for User Story 1

- [ ] T010 [P] [US1] 在 `tests/providers/anomaly_detector_provider/test_anomaly_detector_provider.py` 中编写用例：当 Prometheus 返回正常数据且未检测到异常时，断言日志中包含状态 `normal`、`anomaly_count=0` 以及查询参数字段
- [ ] T011 [P] [US1] 在同一测试文件中编写用例：当检测到异常点（可通过构造数据或 mock 检测结果）时，断言日志中包含状态 `success`、正确的 `anomaly_count` 以及统计信息字段
- [ ] T012 [P] [US1] 在同一测试文件中编写用例：当 Prometheus/Tempo/Loki 查询返回空结果时，断言日志中包含状态 `no_data` 且记录空结果原因
- [ ] T013 [P] [US1] 在同一测试文件中编写用例：当数据点不足（少于最小阈值）时，断言日志中包含状态 `insufficient_data`、`data_points` 与 `min_data_points`

### Implementation for User Story 1

- [ ] T014 [US1] 在 `keep/providers/anomaly_detector_provider/anomaly_detector_provider.py` 的 `_query` 方法中，补全或调整“检测开始”日志（INFO 级别），确保 `extra` 字段包含 `metric`、`data_source`、`time_range`、`algorithm` 以及上下文字段（`_get_logging_context`）
- [ ] T015 [US1] 在 `_query` 内针对 Prometheus/Tempo/Loki 查询执行处，确保记录“查询执行”日志（INFO 级别），并在 `extra` 中添加 `query`、`range_seconds` 等字段（对应 data-model 与 contracts 定义）
- [ ] T016 [US1] 在数据获取后（各数据源分支中），确保记录“数据获取成功/失败”日志：成功时 INFO，失败或无数据时 WARNING，并在 `extra` 中包含 `data_points`、`min_data_points`（如果适用）与 `status`
- [ ] T017 [US1] 在异常检测算法执行后，增加 DEBUG 级别日志，记录统计信息（`min_value`、`max_value`、`mean`、`std`）以及 `anomalies_summary`（仅记录前 10 个异常点）
- [ ] T018 [US1] 在检测完成逻辑中统一输出“检测完成”日志：根据结果选择 INFO/WARNING 级别，确保 `extra` 中包含 `status`、`total_points`、`anomaly_count`、`mean`、`std`、`algorithm` 等字段，并覆盖正常、有异常、无数据、数据不足四种状态
- [ ] T019 [US1] 在 `_query` 的异常处理分支中（包括数据源连接失败、查询超时、数据解析错误、未预期异常），确保记录 ERROR 级别日志，`extra` 中包含 `error_message` 和（在需要时）`error_traceback`
- [ ] T020 [US1] 通过运行测试文件 `tests/providers/anomaly_detector_provider/test_anomaly_detector_provider.py`，修复直至与 User Story 1 相关的所有用例全部通过

**Checkpoint**: 完成以上任务后，User Story 1 应该可以独立测试：任意一次异常检测调用都会产生至少一条包含完整检测结果和上下文信息的日志记录。

---

## Phase 4: User Story 2 - 日志格式与独立服务保持一致 (Priority: P2)

**Goal**: Provider 模式下的日志格式和内容在关键信息上与之前独立异常检测服务保持一致或相似，使用户能够复用原有的日志查询与分析方式。

**Independent Test**: 选取历史独立服务日志样本，与 Provider 产生的日志对比，确认关键字段和语义一致；在 Loki 或其他日志系统中使用原有查询语句（仅调整服务名称或标签），可以成功检索 Provider 日志。

### Tests for User Story 2

- [ ] T021 [P] [US2] 在 `tests/providers/anomaly_detector_provider/test_anomaly_detector_provider.py` 中添加对比测试：构造一条模拟“正常完成”的检测流程，断言 Provider 日志中的字段集合与 `specs/001-anomaly-detector-provider` 或旧服务示例中的关键字段一致（如 `metric`、`status`、`anomaly_count` 等）
- [ ] T022 [P] [US2] 在测试中验证：使用与旧服务相同或相似的 LogQL 查询（参考 `contracts/logging-api.md` 和 `quickstart.md`）可以成功筛选出 Provider 日志

### Implementation for User Story 2

- [ ] T023 [US2] 查阅旧异常检测服务日志格式（包括 `docker-compose-with-otel.yaml` 中异常检测服务日志样例以及 `specs/001-anomaly-detector-provider` 相关文档），整理关键字段和典型日志消息模板
- [ ] T024 [US2] 在 `anomaly_detector_provider.py` 中统一和优化日志消息文本，使“检测开始”“查询执行”“数据获取”“检测完成”“错误”等消息在语义和关键字段上与旧服务日志尽量对齐
- [ ] T025 [US2] 根据 `contracts/logging-api.md` 要求，检查并调整所有日志记录的 `extra` 字段键名，确保使用 snake_case 命名并与旧服务字段保持兼容（如 `data_source`、`time_range`、`anomaly_count` 等）
- [ ] T026 [US2] 如果 Provider 当前日志缺少旧服务中常用的某些字段（例如某些统计信息或查询表达式），在不破坏现有结构的前提下补充这些字段到 `extra`
- [ ] T027 [US2] 运行与 US2 相关的测试用例，确保日志字段和消息格式满足对齐要求

**Checkpoint**: 完成以上任务后，User Story 2 应该可以独立验证：对比旧服务与 Provider 的日志，在关键字段和查询方式上具有良好兼容性。

---

## Phase 5: User Story 3 - 日志可被前端界面或日志聚合系统收集 (Priority: P3)

**Goal**: 确保异常检测 Provider 产生的日志可以被 Keep 前端界面、Loki 或其他日志聚合系统正常收集、索引和展示，并通过统一界面查看所有异常检测活动。

**Independent Test**: 在配置好日志聚合系统后，执行包含异常检测步骤的工作流；通过 Loki/ELK 或 Keep 前端日志界面，按 `provider_type="anomaly_detector"`、`workflow_id`、`step_id` 等条件进行查询，确认日志完整可见。

### Tests for User Story 3

- [ ] T028 [P] [US3] 在集成测试或手动测试脚本中，执行一个包含异常检测步骤的工作流，通过 Loki LogQL（示例在 `quickstart.md` 与 `contracts/logging-api.md`）查询 `provider_type="anomaly_detector"` 的日志，验证能看到对应工作流的检测日志
- [ ] T029 [P] [US3] 如环境支持，通过 Keep 前端“工作流执行详情”页面查看日志，验证异常检测步骤的详细日志可见且包含检测参数与结果

### Implementation for User Story 3

- [ ] T030 [US3] 确认 `anomaly_detector_provider.py` 使用模块级 `logger = logging.getLogger(__name__)`，且未替换为自定义 handler，从而确保日志通过后端统一日志管道输出
- [ ] T031 [US3] 在 `_get_logging_context` 中确保 `provider_type="anomaly_detector"`、`workflow_id`、`workflow_execution_id`、`step_id`、`tenant_id` 均被正确设置并出现在所有检测相关日志的 `extra` 中
- [ ] T032 [US3] 根据 `keep/api/logging.py` 配置，验证异常检测日志会被 `workflowhandler` / `default` handler 处理，并在必要时为工作流相关日志添加额外标签（避免影响其他模块）
- [ ] T033 [US3] 检查并更新 `docs` 或部署文档（如 `docs/deployment/configuration.mdx` 或相关 README），说明如何在 Loki/ELK 中查询 `provider_type="anomaly_detector"` 日志以及常用查询示例
- [ ] T034 [US3] 运行 US3 相关测试与手动验证步骤，确保在目标日志系统与前端界面中均可看到异常检测日志

**Checkpoint**: 完成以上任务后，User Story 3 应可独立验证：在配置好的日志聚合系统或前端界面中，可以方便地查询和浏览异常检测 Provider 的日志。

---

## Phase 6: Polish & Cross-Cutting Concerns

**Purpose**: 对所有用户故事完成后的交叉关注点进行收尾，包括文档、性能与维护性。

- [ ] T035 [P] 在 `specs/002-anomaly-detection-logs/quickstart.md` 中补充或校验示例查询语句，确保与最终实现一致
- [ ] T036 更新 `specs/002-anomaly-detection-logs/spec.md` 与 `plan.md` 中与实现不一致的描述（如有），保持文档与代码同步
- [ ] T037 [P] 对 `anomaly_detector_provider.py` 进行适度重构与代码清理（如抽取重复的日志构造逻辑），确保可读性与可维护性
- [ ] T038 [P] 视需要补充更多单元测试或边界场景测试（如极端时间范围、超大数据量），文件：`tests/providers/anomaly_detector_provider/test_anomaly_detector_provider.py`
- [ ] T039 对整体日志记录性能进行简单基准测试（例如对同一指标多次快速执行异常检测），验证日志记录开销不超过总执行时间的 5%，并在需要时优化

---

## Dependencies & Execution Order（依赖关系与执行顺序）

### Phase Dependencies

- **Setup (Phase 1)**: 无前置依赖，可立即开始
- **Foundational (Phase 2)**: 依赖 Setup 完成，阻塞所有用户故事
- **User Stories (Phase 3–5)**: 均依赖 Foundational 完成
  - User Story 1（P1）为 MVP，应优先完成
  - User Story 2（P2）与 User Story 3（P3）可在 US1 完成后并行推进，或按优先级顺序串行推进
- **Polish (Phase 6)**: 依赖所有计划实现的用户故事完成

### User Story Dependencies

- **User Story 1 (P1)**: 仅依赖 Phase 1–2，可独立实现和测试，是最小可用增量（MVP）
- **User Story 2 (P2)**: 依赖 User Story 1 已提供完整日志基础，重点在“与旧服务格式对齐”
- **User Story 3 (P3)**: 依赖 User Story 1 提供完整日志、依赖 Phase 1–2 的日志基础设施，侧重“日志可被聚合系统与前端收集展示”

### Within Each User Story

- 测试任务（T010–T013, T021–T022, T028–T029）应在实现前编写，并在实现前处于 FAIL 状态
- 先完善公共工具与上下文（如 `_get_logging_context`），再完善具体日志记录点
- 日志实现完成后，再运行并修复所有相关测试，确保每个故事可独立通过验证

### Parallel Opportunities（可并行机会）

- Setup 与 Foundational 中标记了 [P] 的任务（T002, T003, T005, T007, T008, T009）可以在不同开发者之间并行推进
- 在 Foundational 完成后：
  - US1 内部标记 [P] 的测试任务（T010–T013）可以并行编写与运行
  - US2 与 US3 可以由不同开发者并行推进，但需在 US1 完成后开始
- Polish 阶段中标记 [P] 的任务（T035, T037, T038, T039 部分工作）同样可以并行完成

---

## Implementation Strategy（实现策略）

### MVP First（仅实现 User Story 1）

1. 完成 Phase 1（Setup）与 Phase 2（Foundational）
2. 实现并通过 Phase 3（User Story 1）的所有任务与测试（T010–T020）
3. 在日志聚合系统或本地日志中验证：所有异常检测执行均有完整日志
4. 在此基础上即可交付一个可观测性完整的 MVP

### Incremental Delivery（增量式交付）

1. 完成 Setup + Foundational → 日志基础设施就绪  
2. 添加 User Story 1 → 测试通过 → 可作为首次交付 / 演示  
3. 添加 User Story 2 → 确保与旧服务格式兼容 → 再次交付 / 演示  
4. 添加 User Story 3 → 确保前端与日志聚合系统集成良好 → 完整方案交付  

### Parallel Team Strategy（多开发者协作）

1. 全队共同完成 Phase 1–2 的基础设施与工具  
2. 之后按以下方式分工：  
   - 开发者 A：主攻 User Story 1（核心日志点与测试）  
   - 开发者 B：在 US1 稳定后负责 User Story 2（兼容旧服务日志）  
   - 开发者 C：在基础能力稳定后负责 User Story 3（日志聚合与前端可见性）  
3. 最后由任意一位或多人协作完成 Phase 6 的文档、性能与收尾工作  

---

## 统计与范围总结

- **总任务数**: 39  
- **按用户故事划分**:  
  - Setup + Foundational（Phase 1–2）: 9 个任务  
  - User Story 1（Phase 3，P1，MVP）: 11 个任务（T010–T020）  
  - User Story 2（Phase 4，P2）: 7 个任务（T021–T027）  
  - User Story 3（Phase 5，P3）: 7 个任务（T028–T034）  
  - Polish（Phase 6）: 5 个任务（T035–T039）  
- **并行任务机会**: 多个标记为 [P] 的任务可在不同开发者间并行执行  
- **MVP 范围**: 完成 User Story 1（Phase 3）即可形成首个可用版本，满足“看到所有异常检测执行日志”的核心需求  

# Tasks: 异常检测日志记录功能

**Feature Branch**: `002-anomaly-detection-logs`  
**Date**: 2025-01-27  
**Spec**: [spec.md](./spec.md) | **Plan**: [plan.md](./plan.md)

## Summary

本功能为 Anomaly Detector Provider 添加完整的日志记录功能，确保无论检测结果如何（正常、异常、无数据、数据不足等），都能在日志系统中看到完整的异常检测执行信息。

**总任务数**: 18  
**用户故事任务数**: 
- User Story 1 (P1): 8 个任务
- User Story 2 (P2): 4 个任务
- User Story 3 (P3): 3 个任务
- 收尾阶段: 3 个任务

**并行执行机会**: 测试任务可以并行执行

**独立测试标准**: 
- User Story 1: 在工作流中调用 Anomaly Detector provider 进行异常检测，无论检测结果如何，都应在日志系统中找到包含检测参数、执行过程、数据统计和检测结果的完整日志记录
- User Story 2: 对比独立异常检测服务的历史日志格式与 Provider 模式下的日志格式，确认关键字段在两种模式下都能以相似的方式被提取和查询
- User Story 3: 配置 Keep 系统使用 Loki 或其他日志聚合系统，执行包含异常检测的工作流，确认日志能够被正确收集并在日志查看界面中可见

**建议 MVP 范围**: User Story 1（查看所有异常检测执行日志）- 这是核心功能，其他故事可以在此基础上增量交付

## Implementation Strategy

**MVP 优先**: 首先实现 User Story 1，确保基本的日志记录功能可用。然后逐步增强日志格式一致性（User Story 2）和日志聚合系统集成（User Story 3）。

**增量交付**: 每个用户故事都是独立可测试的，可以分阶段交付和验证。

## Dependencies

```
Phase 1 (Setup)
  ↓
Phase 2 (Foundational)
  ↓
Phase 3 (User Story 1 - P1) ← MVP
  ↓
Phase 4 (User Story 2 - P2)
  ↓
Phase 5 (User Story 3 - P3)
  ↓
Phase 6 (Polish)
```

**用户故事依赖关系**:
- User Story 1 是基础，必须首先完成
- User Story 2 依赖 User Story 1（需要先有日志记录才能比较格式）
- User Story 3 依赖 User Story 1 和 User Story 2（需要先有日志记录和格式一致性才能测试聚合系统）

## Parallel Execution Examples

### User Story 1 阶段
- T003 [P] [US1] 和 T004 [P] [US1] 可以并行执行（测试和实现代码在不同文件）

### User Story 2 阶段
- T011 [P] [US2] 和 T012 [P] [US2] 可以并行执行（不同测试文件）

## Phase 1: Setup

### 目标
准备开发环境和项目结构。

### 独立测试标准
项目结构已创建，可以导入必要的模块。

---

- [x] T001 确认开发环境配置，包括 Python 3.11+ 和必要的依赖包

## Phase 2: Foundational

### 目标
实现获取上下文信息的辅助方法，为后续日志记录提供基础。

### 独立测试标准
能够从 ContextManager 和线程上下文获取工作流 ID、步骤 ID、租户 ID 等信息。

---

- [x] T002 在 `AnomalyDetectorProvider` 类中添加获取上下文信息的辅助方法 `_get_logging_context()`，从 `self.context_manager` 获取 `workflow_id`、`workflow_execution_id`、`tenant_id`，从线程上下文获取 `step_id`，返回包含所有上下文信息的字典，文件路径：`keep/providers/anomaly_detector_provider/anomaly_detector_provider.py`

## Phase 3: User Story 1 - 查看所有异常检测执行日志 (P1)

### 目标
实现完整的日志记录功能，确保无论检测结果如何，都能在日志系统中看到完整的异常检测执行信息。

### 独立测试标准
在工作流中调用 Anomaly Detector provider 进行异常检测，无论检测结果如何（正常、异常、无数据、数据不足等），都应在日志系统中（如 Loki、标准输出或 Keep 的日志聚合系统）找到包含检测参数、执行过程、数据统计和检测结果的完整日志记录。

### 验收场景
1. **Given** 已配置并安装 Anomaly Detector provider，**When** 在工作流中调用该 provider 对某个 Prometheus 指标执行异常检测，**Then** 在日志系统中能够找到包含以下信息的日志条目：检测开始时间、查询的指标名称、数据源类型、查询时间范围、从数据源获取的数据点数量、检测算法类型、检测执行状态、统计信息（均值、标准差等）、检测结果（是否发现异常、异常数量）。
2. **Given** 上述场景中检测结果为"正常"（未发现异常），**When** 查看日志系统，**Then** 仍然能够看到完整的检测执行日志，包括"状态=正常"、"异常数量=0"等信息，而不仅仅是错误或警告级别的日志。
3. **Given** 检测过程中出现数据不足、查询失败等非异常情况，**When** 查看日志系统，**Then** 能够看到清晰的日志记录，说明数据不足的原因（如数据点数量少于最小要求）或查询失败的具体错误信息。

---

- [x] T003 [P] [US1] 创建单元测试文件 `test_logging.py`，测试日志记录功能，包括测试检测开始日志、查询执行日志、数据获取日志、检测完成日志等，文件路径：`tests/providers/anomaly_detector_provider/test_logging.py`

- [x] T004 [P] [US1] 在 `_query()` 方法开始处添加检测开始日志（INFO 级别），包含 `metric`、`data_source`、`time_range`、`algorithm` 等检测参数，使用 `_get_logging_context()` 获取上下文信息并通过 `extra` 参数传递，文件路径：`keep/providers/anomaly_detector_provider/anomaly_detector_provider.py`

- [x] T005 [US1] 在查询执行后添加查询执行日志（INFO 级别），包含实际执行的查询表达式（`query_expr`）、数据源类型、查询时间范围等信息，文件路径：`keep/providers/anomaly_detector_provider/anomaly_detector_provider.py`

- [x] T006 [US1] 在数据获取后添加数据获取日志（INFO 级别用于成功，WARNING 级别用于失败），包含从数据源获取的数据点数量，如果数据不足则包含当前数据点数量和最小要求数量，文件路径：`keep/providers/anomaly_detector_provider/anomaly_detector_provider.py`

- [x] T007 [US1] 在检测算法执行后添加算法执行细节日志（DEBUG 级别），包含数据统计信息（最小值、最大值、均值、标准差等），使用 `logger.isEnabledFor(logging.DEBUG)` 检查日志级别以避免不必要的计算，文件路径：`keep/providers/anomaly_detector_provider/anomaly_detector_provider.py`

- [x] T008 [US1] 在检测完成后添加检测完成日志（INFO 级别用于正常/有异常，WARNING 级别用于无数据/数据不足），包含检测状态、总数据点数量、异常数量、均值、标准差、算法类型等完整信息，确保正常结果也记录 INFO 级别日志，文件路径：`keep/providers/anomaly_detector_provider/anomaly_detector_provider.py`

- [x] T009 [US1] 在检测到异常时添加异常详情日志（WARNING 级别），包含异常数量和异常点摘要（前 10 个异常点，包含 index、value、score），文件路径：`keep/providers/anomaly_detector_provider/anomaly_detector_provider.py`

- [x] T010 [US1] 在错误处理中添加错误日志（ERROR 级别），包含详细的错误信息和堆栈跟踪（使用 `exc_info=True`），确保所有错误情况都有适当的日志记录，文件路径：`keep/providers/anomaly_detector_provider/anomaly_detector_provider.py`

## Phase 4: User Story 2 - 日志格式与独立服务保持一致 (P2)

### 目标
确保 Provider 模式下的日志格式和内容与之前独立服务产生的日志在关键信息上保持一致或相似。

### 独立测试标准
对比独立异常检测服务（如 `keep-anomaly-detector` 容器）的历史日志格式与 Provider 模式下的日志格式，确认关键字段（如指标名称、时间范围、检测结果、异常数量等）在两种模式下都能以相似的方式被提取和查询。

### 验收场景
1. **Given** 独立异常检测服务在日志中记录了指标名称、查询时间范围、数据点数量、检测算法、异常数量等字段，**When** Provider 模式执行相同指标的检测，**Then** Provider 的日志中也包含这些相同或等价的字段，且字段命名和含义保持一致。
2. **Given** 用户使用 LogQL 或类似的日志查询语言在 Loki 中查询独立服务的异常检测日志，**When** 切换到 Provider 模式后，**Then** 用户能够使用相同或相似的查询语句查询 Provider 的异常检测日志，只需调整日志来源标识（如服务名称或标签）。

---

- [x] T011 [P] [US2] 创建测试文件验证日志字段一致性，对比独立服务的日志字段与 Provider 日志字段，确保关键字段（指标名称、时间范围、数据点数量、检测算法、异常数量等）都存在且命名一致，文件路径：`tests/providers/anomaly_detector_provider/test_logging_format.py`

- [x] T012 [P] [US2] 创建测试文件验证 LogQL 查询兼容性，测试使用 LogQL 查询 Provider 日志时能够提取与独立服务相同的字段，文件路径：`tests/providers/anomaly_detector_provider/test_logql_compatibility.py`

- [x] T013 [US2] 检查并调整日志字段命名，确保与独立服务使用的字段命名保持一致（如 `metric`、`time_range`、`data_points`、`algorithm`、`anomaly_count` 等），文件路径：`keep/providers/anomaly_detector_provider/anomaly_detector_provider.py`

- [x] T014 [US2] 确保日志消息格式与独立服务保持一致，使用相似的日志消息模板和格式，便于用户迁移，文件路径：`keep/providers/anomaly_detector_provider/anomaly_detector_provider.py`

## Phase 5: User Story 3 - 日志可被前端界面或日志聚合系统收集 (P3)

### 目标
确保异常检测 Provider 产生的日志能够被 Keep 的前端界面、Loki 日志系统或其他日志聚合工具正常收集、索引和展示。

### 独立测试标准
配置 Keep 系统使用 Loki 或其他日志聚合系统，执行包含异常检测的工作流，确认日志能够被正确收集并在日志查看界面中可见，且包含适当的标签和元数据以便过滤和搜索。

### 验收场景
1. **Given** Keep 系统已配置 Loki 作为日志聚合系统，**When** 执行包含 Anomaly Detector provider 的工作流，**Then** 在 Loki 中能够查询到该 provider 产生的异常检测日志，且日志包含适当的标签（如 `provider=anomaly_detector`、`workflow_id=xxx` 等）以便过滤。
2. **Given** Keep 前端界面提供了日志查看功能，**When** 用户在工作流执行详情页面查看日志，**Then** 能够看到该工作流中异常检测步骤的详细日志，包括检测参数、执行过程和结果。

---

- [x] T015 [P] [US3] 创建集成测试文件，测试日志能够被 Loki 正确收集和查询，验证日志包含适当的标签（`provider_type`、`workflow_id`、`step_id`、`tenant_id` 等），文件路径：`tests/providers/anomaly_detector_provider/test_logging_integration.py`

- [x] T016 [US3] 确保所有日志记录都包含 `provider_type="anomaly_detector"` 标签，以便在日志系统中进行过滤，文件路径：`keep/providers/anomaly_detector_provider/anomaly_detector_provider.py`

- [x] T017 [US3] 验证日志格式符合 Keep 的日志标准，确保 JSON 格式日志能够被日志聚合系统正确解析和索引，文件路径：`keep/providers/anomaly_detector_provider/anomaly_detector_provider.py`

## Phase 6: Polish & Cross-Cutting Concerns

### 目标
完善功能，处理边界情况，优化性能，确保代码质量。

---

- [x] T018 添加性能测试，验证日志记录不会显著影响异常检测的执行性能，确保性能开销不超过检测总执行时间的 5%，文件路径：`tests/providers/anomaly_detector_provider/test_logging_performance.py`

- [x] T019 处理边界情况：当日志系统不可用或日志写入失败时，确保不影响异常检测功能的正常执行，添加适当的错误处理，文件路径：`keep/providers/anomaly_detector_provider/anomaly_detector_provider.py`

- [x] T020 代码审查和优化：检查所有日志记录代码，确保遵循最佳实践（预先计算值、限制日志数据量、使用适当的日志级别等），文件路径：`keep/providers/anomaly_detector_provider/anomaly_detector_provider.py`

