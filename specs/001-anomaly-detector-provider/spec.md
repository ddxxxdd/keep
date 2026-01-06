# Feature Specification: Anomaly Detector as Custom Provider

**Feature Branch**: `001-anomaly-detector-provider`  
**Created**: 2025-12-29  
**Updated**: 2025-01-27  
**Status**: Draft  
**Input**: User description: "我需要给当前的keep平台添加一个自定义provider，这是一个异常检测的provider，可以从Prometheus服务中获取指标信息，然后根据相应的异常检测算法来检测指标是否发生了异常，当发现异常之后，还需要随之给keep平台发送相应的告警，对001分支进行全面的修改，所有描述文件全部使用中文"

## User Scenarios & Testing *(mandatory)*

### User Story 1 - 以自定义 Provider 方式启用异常检测 (Priority: P1)

作为 Keep 的使用者，我希望不再依赖 `docker-compose-with-otel.yaml` 中单独的“异常检测后台服务”容器，而是通过在 Keep 中安装并配置一个“Anomaly Detector”自定义 Provider，就可以对 Prometheus 指标、Tempo 链路追踪和 Loki 日志进行异常检测，从而在不增加额外后台进程的前提下获得与之前相同的监控效果。

**Why this priority**: 这是迁移需求的核心目标，决定是否可以去掉旧的后台服务，同时保持现有的监控能力和告警可靠性。

**Independent Test**: 在本地或测试环境中仅启用 Keep 主服务和 Prometheus/Tempo/Loki，不启动旧的异常检测容器；在 Keep UI 中安装并配置 Anomaly Detector provider，执行包含该 provider 的测试工作流，确认可以正常对指定 Prometheus 指标、选定的 traces 或 logs 返回异常检测结果。

**Acceptance Scenarios**:

1. **Given** 环境中运行着 `keep-backend-dev`、`keep-frontend-dev` 和 `prometheus`，但未启动任何旧的 `keep-anomaly-detector` 容器，**When** 用户在 UI 中安装并配置“Anomaly Detector” provider，**Then** provider 安装成功并在 Providers 列表中标记为可查询（can_query=true）。
2. **Given** 已成功安装并配置“Anomaly Detector” provider，**When** 用户在工作流中添加一个步骤，使用该 provider 查询某个实际存在的 Prometheus 指标，**Then** 工作流运行成功且步骤返回包含状态字段（如 `status`、`anomaly_count`）的检测结果。

---

### User Story 2 - 行为和敏感度与旧服务保持一致 (Priority: P2)

作为运维/开发人员，我希望通过自定义 Provider 实现的异常检测，在默认参数或推荐配置下，和之前 `docker-compose-with-otel.yaml` 中的异常检测容器在同类指标上的检测结果和敏感度基本一致，以避免迁移后出现大量漏报或误报。

**Why this priority**: 如果迁移后检测行为明显变化，会影响现有告警策略和对系统健康的判断，需要尽量保持“体感一致”。

**Independent Test**: 对同一条 Prometheus 指标，在相同时间窗口内，对比旧服务配置（文档/历史记录）与新 provider 推荐配置，在典型“正常”、“轻微波动”和“明显异常”三种场景下的输出差异，确认阈值和行为在可接受范围内。

**Acceptance Scenarios**:

1. **Given** 旧异常检测服务在 `ANOMALY_DETECTOR_INTERVAL=30`、`ANOMALY_DETECTOR_MIN_DATA_POINTS=10`、`ANOMALY_DETECTOR_QUERY_STEP=15s`、`ANOMALY_DETECTOR_QUERY_RANGE=1800`、`ANOMALY_DETECTOR_ALGORITHM=zscore`、`ANOMALY_DETECTOR_RATE_CHANGE_THRESHOLD=0.3` 等参数下运行，**When** 在新 provider 中按照对等含义设置 `min_data_points`、`query_step`、`query_range_seconds`、`algorithm`、`rate_change_threshold` 等参数，**Then** 对同一时间范围内的请求流量指标，正常场景下两者均不报异常。
2. **Given** 上述配置和一次“明显突增”的流量注入实验，**When** 比较新旧两种实现的输出，**Then** 新 provider 在相同或相近时间点上标记出异常，且异常数量在可接受偏差范围内（例如偏差不超过 20%）。

---

### User Story 3 - 工作流可灵活使用异常检测结果 (Priority: P3)

作为工作流编写者，我希望在步骤中调用 Anomaly Detector provider 时，可以像使用其他数据类 provider 一样，在后续步骤中基于检测结果进行条件判断、分支选择和通知触发，从而用统一的工作流机制消费异常检测结果。

**Why this priority**: 异常检测从"后台自动推送"改为"按需查询"，只有在工作流中方便地使用结果，迁移才真正可落地。

**Independent Test**: 构建一个简单工作流：先调用 Anomaly Detector provider 得到异常检测结果，再根据 `status` 或 `anomaly_count` 是否大于 0 决定是否发送通知；验证在有/无异常的情况下工作流行为符合预期。

**Acceptance Scenarios**:

1. **Given** 一个包含两步的工作流（第一步为 Anomaly Detector provider 检测，第二步为通知动作），**When** 指标在正常范围内，**Then** 第一阶段返回 `status=normal` 且第二步不会发送通知。
2. **Given** 同一工作流在注入异常流量后再次执行，**When** provider 返回 `status=success` 且 `anomaly_count>0`，**Then** 第二步会根据检测结果发送通知并在执行详情中能看到异常点信息。

---

### User Story 4 - 检测到异常后自动发送告警到 Keep 平台 (Priority: P1)

作为运维人员，我希望 Anomaly Detector provider 在检测到异常时能够自动向 Keep 平台发送告警，就像之前的独立异常检测服务一样，从而无需通过工作流就能及时收到异常通知。

**Why this priority**: 这是用户的核心需求，确保迁移后能够保持与旧服务相同的自动告警能力，无需额外配置工作流即可获得异常通知。

**Independent Test**: 配置 Anomaly Detector provider 并执行异常检测，当检测到异常数量超过配置的阈值时，在 Keep 平台的告警列表中能够看到自动创建的告警，且告警包含完整的异常检测信息。

**Acceptance Scenarios**:

1. **Given** 已配置并安装 Anomaly Detector provider，**When** provider 在执行 `_query` 方法时检测到异常数量超过配置的阈值，**Then** provider 自动向 Keep 平台发送告警，告警在 Keep 前端界面中可见。
2. **Given** 上述场景中检测到的异常数量未超过阈值，**When** provider 执行检测，**Then** 不会发送告警，但检测结果仍然正常返回。
3. **Given** provider 自动发送的告警，**When** 查看告警详情，**Then** 告警包含完整的异常检测信息：指标名称、异常数量、异常点详情（时间戳、数值、评分）、检测算法、统计信息（均值、标准差）等。

---

### Edge Cases

- 当 Prometheus/Tempo/Loki 任一数据源的地址配置错误、不可达或认证失败时，provider 初始化或查询应给出清晰的错误信息，并在 UI 或执行日志中可见，避免"静默失败"。
- 当选定的 Prometheus 指标或 traces/logs 查询在给定时间窗口内没有任何数据点时，provider 应返回 `status=no_data`（或等价标识），而不是抛出未处理异常。
- 当可用数据点数量少于 `min_data_points` 时，provider 应返回 `status=insufficient_data` 并包含当前数据点数量，以便用户理解为何未进行检测。
- 当 Prometheus 指标的基线几乎为 0 时（例如长时间无请求的服务），对于突然出现的非零值，provider 应有合理的异常判断逻辑，避免全部被视为正常或全部视为异常；对 traces/logs 也应在查询条件为空或极少数据时避免误判。
- 当自动发送告警失败时（如 Keep API 不可达、认证失败等），provider 应记录错误日志，但不影响异常检测结果的正常返回，确保检测功能本身不受影响。
- 当同一指标在短时间内多次检测到异常时，应使用 Keep 平台的默认去重机制（基于 fingerprint）避免重复告警，确保告警列表不会因相同异常而产生大量重复条目。

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: 系统必须提供一个名为“Anomaly Detector”的 Provider，在 Providers 列表中以“Monitoring / AI / data”标签分类展示，并支持被安装、配置和卸载。
- **FR-002**: 该 Provider 必须允许用户通过 UI 配置 Prometheus/Tempo/Loki 连接信息，包括各自的 URL、认证方式（如用户名/密码或 Token，可选）以及是否校验 SSL 证书，使其能够接入指标、链路和日志三类数据源。
- **FR-003**: 该 Provider 必须允许用户在 UI 中配置异常检测相关核心参数，包括：检测算法（`isolation_forest`、`zscore` 或 `both`）、最小数据点数量、历史数据点数量、查询时间范围（秒）、查询步长以及环比变化阈值；对于 traces 和 logs，如采用与指标不同的窗口或粒度，应在配置上给予区分或清晰说明。
- **FR-004**: Provider 在执行查询时，必须从配置的 Prometheus 实例拉取指定指标在给定时间范围内的时间序列数据，并根据配置参数计算异常点；对于 Tempo 和 Loki，应基于相应查询语句或过滤条件获取时间序列或计数类数据，并应用一致的异常检测逻辑。
- **FR-005**: Provider 的 `_query`/`query` 方法必须返回结构化结果，至少包含：原始指标名或 PromQL、状态字段（如 `success`/`normal`/`no_data`/`insufficient_data`）、总数据点数量、异常数量以及异常点列表（含索引、数值和评分）。
- **FR-006**: 系统必须允许在工作流步骤中以 `provider.type = anomaly_detector` 的方式引用该 Provider，并通过 `with` 字段传入待检测的 `metric`（指标名或完整 PromQL）以及可选的 `time_range` 参数。
- **FR-007**: 当 Prometheus 请求失败、返回数据格式异常或内部检测过程抛出错误时，Provider 必须记录带有上下文信息的日志，并向调用方返回可理解的错误信息，而不是无提示地中断工作流。
- **FR-008**: 对于计数型指标（如以 `_count`、`_sum`、`_total`、`_bucket` 结尾的指标），Provider 必须自动按原有服务约定使用 `rate()` 包装查询（例如 `rate(metric[3m])`），以保持与旧实现相同的语义；对于 traces/logs 这类事件型数据，应在设计上提供等价的“速率”或频次分析能力。
- **FR-009**: 在不配置任何“旧服务特有环境变量”（如 `ANOMALY_DETECTOR_*`）的情况下，只要安装并配置了 Anomaly Detector provider，现有 Keep 主服务和 docker-compose 编排文件不应再依赖单独的异常检测后台容器即可完成异常检测。
- **FR-010**: 文档或配置示例中必须给出一份推荐配置，说明如何在 UI 中设置各个字段以复现 `docker-compose-with-otel.yaml` 中被注释掉的异常检测服务效果，便于用户迁移。
- **FR-011**: Provider 在 `_query` 方法中检测到异常时，必须自动向 Keep 平台发送告警，完全模拟旧独立服务的自动告警行为，无需通过工作流即可触发告警。
- **FR-012**: Provider 必须支持配置异常数量阈值（`min_anomaly_count_for_alert`），只有当检测到的异常数量大于等于该阈值时才发送告警；该阈值应在 provider 配置中可设置，默认值为 1（即 `anomaly_count >= 1`）。
- **FR-013**: Provider 自动发送的告警必须包含完整的异常检测信息，至少包括：告警名称（基于指标名称）、告警描述（包含异常数量、检测算法、统计信息等）、告警严重程度、异常点详情（时间戳、数值、评分）、检测时间等。
- **FR-014**: Provider 必须为自动发送的告警生成合适的 fingerprint，以便 Keep 平台能够正确进行告警去重，避免相同指标的重复告警；fingerprint 应基于指标名称和关键标签。
- **FR-015**: 当自动发送告警失败时（如 Keep API 调用失败、网络错误等），Provider 必须记录详细的错误日志，但不影响异常检测结果的正常返回，确保检测功能本身不受告警发送失败的影响。
- **FR-016**: Provider 必须支持一种“批量检测模式”，在该模式下无需在工作流中逐条写出具体 Prometheus 指标名：系统通过 Provider 配置中的包含/排除规则（如 `include_metrics`/`exclude_metrics`，支持前缀或正则）以及全局最大指标数量上限（例如最多检测 100 条指标），从 Prometheus 自动发现一组候选指标集合，仅对这组候选指标执行异常检测，并对每个检测到异常的指标分别自动发送告警。
- **FR-017**: 在“批量检测模式”下，Provider 必须返回至少包含以下字段的汇总结果：运行模式（单指标/批量）、本次检测的指标总数、实际执行异常检测的指标数量、已发送告警的指标数量，以及每个指标对应的检测结果摘要（包括指标名、状态、异常数量等），以便在调试和验证时查看整体检测情况。

### Key Entities *(include if feature involves data)*

- **Anomaly Detector Provider**: 表示在 Keep 中配置的一组异常检测能力，包含 Prometheus/Tempo/Loki 等数据源的连接信息和检测参数（算法、时间窗口、阈值等），供工作流步骤在运行时引用。
- **Anomaly Detection Result**: 单次调用 Provider 对某个指标、traces 查询或日志查询进行检测后产生的结果对象，包含数据源与查询标识、查询时间范围、状态、统计信息（均值、标准差等）以及零个或多个异常点。
- **Workflow Step Using Anomaly Detector**: 工作流中的一个“查询”步骤，引用某个已安装的 Anomaly Detector provider，并通过参数指定具体要检测的 Prometheus 指标、traces 查询或日志查询以及时间范围。

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: 在不启动旧 `keep-anomaly-detector` 容器的前提下，至少 95% 依赖异常检测的现有或新建工作流能够仅通过 Anomaly Detector provider 成功完成并返回检测结果。
- **SC-002**: 使用推荐配置对典型 HTTP 请求指标进行故障注入测试时，新 provider 检测到的异常点与旧实现的检测结果在“是否发现异常”这一维度上的一致率达到 90% 以上。
- **SC-003**: 在 Prometheus 正常可用的前提下，通过 provider 进行单次异常检测的工作流步骤失败率（因配置错误、处理失败等导致的非预期错误）低于 2%。
- **SC-004**: 在完成迁移并停用旧异常检测容器后，相关的运维反馈或支持工单中，与"异常检测缺失/异常行为"相关的问题数量不高于迁移前一个对比周期。
- **SC-005**: 当检测到异常数量超过配置的阈值时，Provider 自动发送告警的成功率（告警成功创建并出现在 Keep 前端界面）应不低于 95%。
- **SC-006**: 自动发送的告警应正确使用 Keep 平台的去重机制，相同指标的重复告警去重率应达到 100%（即不会出现基于相同 fingerprint 的重复告警）。

## Clarifications

### Session 2025-12-29

- Q: 新 provider 是否需要同时支持 Prometheus 指标、Tempo traces 和 Loki logs 的异常检测？ → A: 需要同时支持指标、traces 和日志

### Session 2025-01-27

- Q: 自动发送告警的触发方式是什么？ → A: 仅在 `_query` 方法中检测到异常时自动发送告警（完全模拟旧服务行为）
- Q: 告警的触发条件是什么？ → A: 只有当异常数量超过某个阈值（如 `anomaly_count >= 3`）时才发送告警
- Q: 异常数量阈值是否可配置？ → A: 阈值可配置（在 provider 配置中设置），默认值为 1（即 `anomaly_count >= 1`）
- Q: 告警内容应包含哪些信息？ → A: 包含完整的异常检测信息（指标名称、异常数量、异常点详情、检测算法、统计信息等）
- Q: 告警去重策略是什么？ → A: 使用 Keep 平台的默认去重机制（基于 fingerprint）

### Session 2026-01-05

- Q: 是否需要支持“无需逐条写 Prometheus 指标名，而是自动检测一组 Prometheus 指标并对其中的异常指标发送告警”？ → A: 需要支持，但应通过 `include_metrics`/`exclude_metrics` 等可配置规则和全局最大指标数量上限来限定候选指标集合，而不是每次无上限地扫描 Prometheus 中的所有指标。

## Assumptions

- Prometheus 服务已部署并可访问，提供标准的 Prometheus Query API
- Keep 平台已部署并运行，支持自定义 Provider 的安装和配置
- 用户具备基本的 Prometheus 指标查询知识（PromQL）
- 异常检测算法（Z-Score、Isolation Forest）的实现已存在于 Keep 平台中
- Keep 平台提供告警处理机制（`process_event` 函数），支持告警的去重和存储
