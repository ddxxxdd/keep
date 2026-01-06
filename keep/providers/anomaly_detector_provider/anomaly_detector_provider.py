"""
异常检测 Provider（AnomalyDetectorProvider）
===========================================

本 Provider 将异常检测能力以 **自定义 Provider** 的形式集成到 Keep 中，
用于在工作流（Workflow）里按需触发 Prometheus 指标、Tempo traces 和 Loki logs 的异常检测。

主要特性：
- 支持多数据源：Prometheus（指标）、Tempo（链路追踪）、Loki（日志）
- 直接从 Prometheus 拉取时间序列数据
- 通过 TraceQL 查询 Tempo traces 并聚合为时间序列
- 通过 LogQL 聚合查询 Loki logs 返回时间序列
- 自动识别计数型指标（*_count / *_sum / *_total / *_bucket），并使用 rate() 计算速率
- 使用滑动窗口 + 基线（Baseline）+ 环比变化阈值进行异常检测
- 配置字段尽量与原来基于环境变量的异常检测服务保持一致（ANOMALY_DETECTOR_*）

注意：
- 当检测到异常数量超过配置的阈值（`min_anomaly_count_for_alert`）时，Provider 会自动向 Keep 平台发送告警，完全模拟旧独立服务的自动告警行为。
- 告警包含完整的异常检测信息，并使用 Keep 平台的去重机制（基于 fingerprint）避免重复告警。
- 告警发送失败不会影响异常检测结果的正常返回。
- 同时，Provider 也通过 ``_query`` 返回检测结果，供工作流使用。
"""



from __future__ import annotations

import dataclasses
import hashlib
import json
import logging
import threading
import time
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import numpy as np
import pydantic

from keep.api.models.alert import AlertDto, AlertSeverity, AlertStatus
from keep.api.tasks.process_event_task import process_event
from keep.contextmanager.contextmanager import ContextManager
from keep.providers.base.base_provider import BaseProvider
from keep.providers.models.provider_config import ProviderConfig

from keep.anomaly_detector.algorithms import (
    AnomalyResult,
    DetectionResult,
)
from keep.anomaly_detector.config import (
    AnomalyDetectorConfig,
    PrometheusConfig,
    TempoConfig,
    LokiConfig,
)
from keep.providers.anomaly_detector_provider.clients.loki_client import LokiClient
from keep.providers.anomaly_detector_provider.clients.prometheus_client import (
    PrometheusClient,
)
from keep.providers.anomaly_detector_provider.clients.tempo_client import TempoClient

logger = logging.getLogger(__name__)


@pydantic.dataclasses.dataclass
class AnomalyDetectorProviderAuthConfig:
    """
    异常检测 Provider 的认证与配置字段。

    字段设计尽量对齐原来的环境变量配置：
    - PROMETHEUS_URL
    - ANOMALY_DETECTOR_*
    等，用于保证迁移到 Provider 形式后行为保持一致。
    """

    prometheus_url: str = dataclasses.field(
        metadata={
            "required": True,
            "description": "Prometheus 服务地址",
            "validation": "any_http_url",
        }
    )

    prometheus_username: str = dataclasses.field(
        default="",
        metadata={
            "description": "Prometheus 用户名",
            "sensitive": False,
        },
    )

    prometheus_password: str = dataclasses.field(
        default="",
        metadata={
            "description": "Prometheus 密码",
            "sensitive": True,
        },
    )

    prometheus_verify_ssl: bool = dataclasses.field(
        default=True,
        metadata={
            "description": "是否校验 Prometheus SSL 证书",
            "hint": "自签名证书可设为 false",
        },
    )

    algorithm: str = dataclasses.field(
        default="zscore",  # 默认值改为 zscore，与旧服务配置一致
        metadata={
            "description": "检测算法",
            "type": "select",
            "options": ["isolation_forest", "zscore", "both"],
        },
    )

    min_data_points: int = dataclasses.field(
        default=10,  # 默认值改为 10，与旧服务配置一致
        metadata={
            "description": "参与检测的最小数据点数量",
        },
    )

    history_size: int = dataclasses.field(
        default=1000,
        metadata={
            "description": "保留用于分析的历史数据点数量",
        },
    )

    query_range_seconds: int = dataclasses.field(
        default=1800,  # 默认值改为 1800 秒（30分钟），与旧服务配置一致
        metadata={
            "description": "Prometheus 查询时间范围（秒，例：1800=最近30分钟）",
        },
    )

    query_step: str = dataclasses.field(
        default="15s",  # 默认值改为 15s，与旧服务配置一致
        metadata={
            "description": "Prometheus 查询步长（如 15s、60s）",
        },
    )

    rate_change_threshold: float = dataclasses.field(
        default=0.3,  # 默认值改为 0.3（+30%），与旧服务配置一致
        metadata={
            "description": "环比变化阈值（例如 0.3 = 相对基线 +30%）",
        },
    )

    tempo_url: str = dataclasses.field(
        default="http://tempo:3200",
        metadata={
            "description": "Tempo 服务地址",
        },
    )

    tempo_enabled: bool = dataclasses.field(
        default=False,
        metadata={
            "description": "是否启用 Tempo traces 异常检测",
        },
    )

    loki_url: str = dataclasses.field(
        default="http://loki:3100",
        metadata={
            "description": "Loki 服务地址",
        },
    )

    loki_enabled: bool = dataclasses.field(
        default=False,
        metadata={
            "description": "是否启用 Loki logs 异常检测",
        },
    )

    min_anomaly_count_for_alert: int = dataclasses.field(
        default=1,
        metadata={
            "description": "触发告警的最小异常数量阈值",
            "hint": "只有当检测到的异常数量 >= 此值时才会发送告警，默认值为 1",
        },
    )

    include_metrics: list[str] = dataclasses.field(
        default_factory=list,
        metadata={
            "description": "批量检测模式下要包含的 Prometheus 指标名称或正则表达式列表",
            "hint": "支持前缀或正则，例如 'keep_http_' 或 '^http_.*_total$'",
        },
    )

    exclude_metrics: list[str] = dataclasses.field(
        default_factory=list,
        metadata={
            "description": "批量检测模式下要排除的 Prometheus 指标名称或正则表达式列表",
            "hint": "用于过滤掉不关心的运行时/系统指标，例如 '^go_.*'、'^process_.*' 等",
        },
    )

    max_metrics: int = dataclasses.field(
        default=100,
        metadata={
            "description": "批量检测模式下一次最多检测的指标数量上限（0 或负数表示使用默认上限 100）",
        },
    )


class AnomalyDetectorProvider(BaseProvider):
    """
    多数据源异常检测 Provider，支持 Prometheus、Tempo 和 Loki。

    在工作流中的典型用法：
    - 在 Keep UI 中安装并配置本 Provider（Prometheus/Tempo/Loki 地址、算法、阈值等）
    - 在步骤中调用 ``query``，传入查询表达式和可选的数据源类型
    - 根据返回的异常列表决定是否发送通知、创建事件等

    示例：
    - Prometheus: ``query(metric="keep_http_server_duration_seconds_bucket")``
    - Tempo: ``query(data_source="tempo", metric='{ .service_name = "frontend" }')``
    - Loki: ``query(data_source="loki", metric='sum(rate({job="varlogs"}[5m]))')``
    """

    PROVIDER_DISPLAY_NAME = "Anomaly Detector"
    PROVIDER_CATEGORY = ["Monitoring", "AI"]
    PROVIDER_TAGS = ["alert", "data"]

    def __init__(
        self,
        context_manager: ContextManager,
        provider_id: str,
        config: ProviderConfig,
    ):
        # 在 validate_config 中完成真正的配置解析与客户端初始化
        # 注意：这些变量在 validate_config() 中会被设置
        # 如果 validate_config() 抛出异常，这些变量会保持为 None
        self._config: Optional[AnomalyDetectorConfig] = None
        self._prometheus_client: Optional[PrometheusClient] = None
        self._tempo_client: Optional[TempoClient] = None
        self._loki_client: Optional[LokiClient] = None
        
        # 调用父类初始化（会调用 validate_config()）
        # 注意：如果 validate_config() 抛出异常，异常会传播，Provider 不会被创建
        super().__init__(context_manager, provider_id, config)
        
        # 记录初始化后的状态（validate_config 应该已经被 super().__init__() 调用）
        if self._config is None or self._prometheus_client is None:
            error_msg = (
                f"AnomalyDetectorProvider initialized but _config or _prometheus_client is None. "
                f"This indicates validate_config() failed silently or was not called. "
                f"Config: {self.config}, "
                f"Authentication: {self.config.authentication if self.config else None}"
            )
            self.logger.error(error_msg)
            # 不在这里抛出异常，让 _query() 方法处理
        else:
            self.logger.debug(
                f"AnomalyDetectorProvider initialized successfully. "
                f"Config: {self.config}, "
                f"Authentication: {self.config.authentication if self.config else None}, "
                f"_config initialized: {self._config is not None}, "
                f"_prometheus_client initialized: {self._prometheus_client is not None}"
            )

    def validate_config(self) -> None:
        """校验并处理 Provider 配置。"""
        self.logger.debug(
            f"validate_config() called. "
            f"Config: {self.config}, "
            f"Authentication: {self.config.authentication if self.config else None}"
        )
        
        # 检查配置是否存在
        if not self.config:
            error_msg = "Provider config is None. Please check provider configuration."
            self.logger.error(error_msg)
            raise ValueError(error_msg)
        
        if not self.config.authentication:
            error_msg = (
                f"Provider authentication config is missing. "
                f"Config object: {self.config}, "
                f"Please ensure the provider is properly configured with 'prometheus_url' field."
            )
            self.logger.error(error_msg)
            raise ValueError(error_msg)
        
        # 检查必需字段
        if not isinstance(self.config.authentication, dict):
            error_msg = (
                f"Provider authentication config must be a dict, "
                f"got {type(self.config.authentication)}: {self.config.authentication}"
            )
            self.logger.error(error_msg)
            raise ValueError(error_msg)
        
        # 检查 prometheus_url 是否存在
        if not self.config.authentication.get("prometheus_url"):
            error_msg = (
                f"Required field 'prometheus_url' is missing in authentication config. "
                f"Available fields: {list(self.config.authentication.keys())}. "
                f"Please configure 'prometheus_url' in the provider settings."
            )
            self.logger.error(error_msg)
            raise ValueError(error_msg)
        
        # 处理字段格式转换：UI 可能提交字符串，但代码期望列表
        # 创建配置字典的副本，避免修改原始配置
        auth_config_dict = dict(self.config.authentication)
        
        # 记录原始配置用于调试
        self.logger.debug(
            f"原始配置字段类型: include_metrics={type(auth_config_dict.get('include_metrics'))}, "
            f"exclude_metrics={type(auth_config_dict.get('exclude_metrics'))}"
        )
        
        # 处理 include_metrics：如果是字符串，转换为列表
        if "include_metrics" in auth_config_dict:
            include_metrics = auth_config_dict["include_metrics"]
            if isinstance(include_metrics, str):
                # 如果是以逗号分隔的字符串，分割成列表
                if include_metrics.strip():
                    auth_config_dict["include_metrics"] = [m.strip() for m in include_metrics.split(",") if m.strip()]
                    self.logger.debug(
                        f"include_metrics 从字符串转换为列表: {auth_config_dict['include_metrics']}"
                    )
                else:
                    auth_config_dict["include_metrics"] = []
            elif not isinstance(include_metrics, list):
                # 如果不是列表也不是字符串，记录警告并使用空列表
                self.logger.warning(
                    f"include_metrics 格式不正确，期望 list 或 str，实际为 {type(include_metrics)}，将使用空列表"
                )
                auth_config_dict["include_metrics"] = []
        else:
            # 如果字段不存在，不设置（让 pydantic 使用 default_factory）
            pass
        
        # 处理 exclude_metrics：如果是字符串，转换为列表
        if "exclude_metrics" in auth_config_dict:
            exclude_metrics = auth_config_dict["exclude_metrics"]
            if isinstance(exclude_metrics, str):
                # 如果是以逗号分隔的字符串，分割成列表
                if exclude_metrics.strip():
                    auth_config_dict["exclude_metrics"] = [m.strip() for m in exclude_metrics.split(",") if m.strip()]
                    self.logger.debug(
                        f"exclude_metrics 从字符串转换为列表: {auth_config_dict['exclude_metrics']}"
                    )
                else:
                    auth_config_dict["exclude_metrics"] = []
            elif not isinstance(exclude_metrics, list):
                # 如果不是列表也不是字符串，记录警告并使用空列表
                self.logger.warning(
                    f"exclude_metrics 格式不正确，期望 list 或 str，实际为 {type(exclude_metrics)}，将使用空列表"
                )
                auth_config_dict["exclude_metrics"] = []
        else:
            # 如果字段不存在，不设置（让 pydantic 使用 default_factory）
            pass
        
        # 处理空值：将空字符串转换为 None，让 pydantic 使用默认值
        if "history_size" in auth_config_dict:
            history_size = auth_config_dict["history_size"]
            if history_size == "" or (isinstance(history_size, str) and not history_size.strip()):
                # 删除该字段，让 pydantic 使用默认值
                auth_config_dict.pop("history_size", None)
        
        if "max_metrics" in auth_config_dict:
            max_metrics = auth_config_dict["max_metrics"]
            if max_metrics == "" or (isinstance(max_metrics, str) and not max_metrics.strip()):
                # 删除该字段，让 pydantic 使用默认值
                auth_config_dict.pop("max_metrics", None)
        
        # 记录转换后的配置用于调试
        self.logger.debug(
            f"转换后的配置字段类型: include_metrics={type(auth_config_dict.get('include_metrics'))}, "
            f"exclude_metrics={type(auth_config_dict.get('exclude_metrics'))}, "
            f"include_metrics值={auth_config_dict.get('include_metrics')}, "
            f"exclude_metrics值={auth_config_dict.get('exclude_metrics')}"
        )
        
        try:
            self.authentication_config = AnomalyDetectorProviderAuthConfig(
                **auth_config_dict
            )
        except Exception as e:
            error_msg = (
                f"Failed to create AnomalyDetectorProviderAuthConfig: {e}. "
                f"原始配置: {self.config.authentication}. "
                f"转换后配置: {auth_config_dict}. "
                f"Please check that all required fields are properly set."
            )
            self.logger.error(error_msg, exc_info=True)
            raise ValueError(error_msg) from e
        
        self.logger.debug(
            f"validate_config() completed successfully. "
            f"prometheus_url: {self.authentication_config.prometheus_url}"
        )

        # 构造与原异常检测配置兼容的 PrometheusConfig
        prometheus_cfg = PrometheusConfig(
            url=self.authentication_config.prometheus_url,
            username=self.authentication_config.prometheus_username,
            password=self.authentication_config.prometheus_password,
            verify_ssl=self.authentication_config.prometheus_verify_ssl,
        )

        # 构造 TempoConfig（如果启用）
        tempo_cfg = TempoConfig(
            url=self.authentication_config.tempo_url,
            enabled=self.authentication_config.tempo_enabled,
        )

        # 构造 LokiConfig（如果启用）
        loki_cfg = LokiConfig(
            url=self.authentication_config.loki_url,
            enabled=self.authentication_config.loki_enabled,
        )

        # 构造 AnomalyDetectorConfig 实例：
        # - 尽量复用原服务的默认值与逻辑
        # - 通过 Provider 配置覆盖关键参数
        self._config = AnomalyDetectorConfig(
            prometheus=prometheus_cfg,
            tempo=tempo_cfg,
            loki=loki_cfg,
            detection_interval=300,  # Provider 模式下不会用到检测间隔
            min_data_points=self.authentication_config.min_data_points,
            max_metrics=self.authentication_config.max_metrics,
            contamination_rate=0.005,  # 与旧服务配置一致（ANOMALY_DETECTOR_CONTAMINATION=0.005）
            zscore_threshold=2.0,  # 与旧服务配置一致（ANOMALY_DETECTOR_ZSCORE_THRESHOLD=2.0）
            history_size=self.authentication_config.history_size,
            query_time_range=self.authentication_config.query_range_seconds,
            query_step=self.authentication_config.query_step,
            include_metrics=self.authentication_config.include_metrics,
            # 如果用户未显式配置排除规则，则使用与旧异常检测服务类似的默认排除列表，
            # 避免将 go/process/promhttp 等运行时指标纳入批量检测范围。
            exclude_metrics=(
                self.authentication_config.exclude_metrics
                if self.authentication_config.exclude_metrics
                else [
                    r"^go_.*",
                    r"^process_.*",
                    r"^promhttp_.*",
                ]
            ),
            rate_change_threshold=self.authentication_config.rate_change_threshold,
            keep_api_url="",  # Provider 不再直接向 Keep API 发送告警
            keep_api_key="",
            tenant_id=self.context_manager.tenant_id,
            enabled=True,
            algorithm=self.authentication_config.algorithm,
        )

        self._prometheus_client = PrometheusClient(self._config)

        # 如果启用了 Tempo，初始化 Tempo 客户端
        if self.authentication_config.tempo_enabled:
            self._tempo_client = TempoClient(self._config)
        else:
            self._tempo_client = None

        # 如果启用了 Loki，初始化 Loki 客户端
        if self.authentication_config.loki_enabled:
            self._loki_client = LokiClient(self._config)
        else:
            self._loki_client = None

        # 记录初始化成功的 INFO 日志
        self.logger.info(
            f"Anomaly Detector Provider 初始化成功: "
            f"Prometheus={self.authentication_config.prometheus_url}, "
            f"算法={self.authentication_config.algorithm}, "
            f"最小数据点={self.authentication_config.min_data_points}, "
            f"查询时间范围={self.authentication_config.query_range_seconds}秒, "
            f"查询步长={self.authentication_config.query_step}, "
            f"环比阈值={self.authentication_config.rate_change_threshold}, "
            f"Tempo={'已启用' if self.authentication_config.tempo_enabled else '未启用'}, "
            f"Loki={'已启用' if self.authentication_config.loki_enabled else '未启用'}",
            extra={
                "prometheus_url": self.authentication_config.prometheus_url,
                "algorithm": self.authentication_config.algorithm,
                "min_data_points": self.authentication_config.min_data_points,
                "query_range_seconds": self.authentication_config.query_range_seconds,
                "query_step": self.authentication_config.query_step,
                "rate_change_threshold": self.authentication_config.rate_change_threshold,
                "tempo_enabled": self.authentication_config.tempo_enabled,
                "loki_enabled": self.authentication_config.loki_enabled,
            }
        )

    def dispose(self) -> None:
        """Provider 释放资源的钩子，目前无需特殊清理。"""
        return

    def _get_logging_context(self) -> Dict[str, Any]:
        """
        获取日志记录的上下文信息。
        
        从 ContextManager 和线程上下文获取工作流 ID、步骤 ID、租户 ID 等信息，
        用于在日志记录中添加结构化字段。
        
        Returns:
            包含上下文信息的字典，包括：
            - provider_type: Provider 类型（固定为 "anomaly_detector"）
            - workflow_id: 工作流 ID（如果可用）
            - workflow_execution_id: 工作流执行 ID（如果可用）
            - step_id: 步骤 ID（如果可用）
            - tenant_id: 租户 ID
        """
        context = {
            "provider_type": "anomaly_detector",
        }
        
        # 从 ContextManager 获取上下文信息
        if self.context_manager:
            if self.context_manager.workflow_id:
                context["workflow_id"] = self.context_manager.workflow_id
            if self.context_manager.workflow_execution_id:
                context["workflow_execution_id"] = self.context_manager.workflow_execution_id
            if self.context_manager.tenant_id:
                context["tenant_id"] = self.context_manager.tenant_id
        
        # 从线程上下文获取步骤 ID
        thread = threading.current_thread()
        step_id = getattr(thread, "step_id", None)
        if step_id is not None:
            context["step_id"] = step_id
        
        return context

    def _query(
        self,
        metric: str,
        data_source: Optional[str] = None,
        time_range: Optional[str] = None,
        **kwargs: Any,
    ) -> Dict[str, Any]:
        """
        对单个指标/查询表达式执行一次异常检测，支持 Prometheus、Tempo 和 Loki 三种数据源。

        Args:
            metric: 查询表达式：
                   - Prometheus: 指标名或完整 PromQL 表达式
                   - Tempo: TraceQL 查询语句（如 '{ .service_name = "frontend" }'）
                   - Loki: LogQL 聚合查询（如 'sum(rate({job="varlogs"}[5m])) by (level)'）
            data_source: 数据源类型，可选值："prometheus"（默认）、"tempo"、"loki"
            time_range: 可选的人类可读时间范围字符串，例如 "30m"、"1h"。
                        若为空，则使用配置中的 ``query_range_seconds``。

        Returns:
            包含检测元信息与异常点列表的字典。
        """
        if not metric:
            raise ValueError("参数 'metric' 不能为空")

        if not self._config or not self._prometheus_client:
            error_msg = (
                "Provider 配置尚未正确初始化。"
                f" _config: {self._config is not None}, "
                f"_prometheus_client: {self._prometheus_client is not None}. "
                "这可能是因为 validate_config() 方法执行失败。"
                f"请检查 Provider 配置是否正确，特别是 'prometheus_url' 字段。"
            )
            log_context = self._get_logging_context()
            self.logger.error(
                error_msg,
                extra={
                    **log_context,
                    "error_message": error_msg,
                    "metric": metric,
                },
                exc_info=False,
            )
            raise RuntimeError(error_msg)

        # 确定数据源类型（默认为 prometheus）
        data_source = data_source or "prometheus"

        # 获取日志上下文信息
        log_context = self._get_logging_context()

        # 批量检测模式：当 metric 为特殊值 "__all__" 且数据源为 Prometheus 时，
        # 根据 include_metrics/exclude_metrics 与最大数量上限，从 Prometheus 自动发现候选指标并逐个执行检测。
        if metric == "__all__":
            if data_source != "prometheus":
                error_msg = "批量检测模式目前仅支持 Prometheus 数据源"
                self.logger.error(
                    error_msg,
                    extra={
                        **log_context,
                        "error_message": error_msg,
                        "data_source": data_source,
                        "metric": metric,
                    },
                )
                raise ValueError(error_msg)
            return self._run_batch_detection(time_range=time_range, log_context=log_context)

        # 添加 INFO 日志：开始执行异常检测
        # 注意：Python logging 模块默认会捕获日志写入异常，不会影响主程序执行
        self.logger.info(
            f"开始执行异常检测: metric={metric}, data_source={data_source}, time_range={time_range or 'default'}",
            extra={
                **log_context,
                "metric": metric,
                "data_source": data_source,
                "time_range": time_range,
                "algorithm": self._config.algorithm if self._config else None,
            }
        )

        # 验证数据源是否已启用
        try:
            if data_source == "tempo":
                if not self.authentication_config.tempo_enabled:
                    error_msg = "数据源 'tempo' 未在 Provider 配置中启用"
                    self.logger.error(
                        error_msg,
                        extra={
                            **log_context,
                            "error_message": error_msg,
                            "data_source": data_source,
                            "metric": metric,
                        },
                    )
                    raise ValueError(error_msg)
                if not self._tempo_client:
                    error_msg = "Tempo 客户端未初始化"
                    self.logger.error(
                        error_msg,
                        extra={
                            **log_context,
                            "error_message": error_msg,
                            "data_source": data_source,
                            "metric": metric,
                        },
                    )
                    raise RuntimeError(error_msg)
            elif data_source == "loki":
                if not self.authentication_config.loki_enabled:
                    error_msg = "数据源 'loki' 未在 Provider 配置中启用"
                    self.logger.error(
                        error_msg,
                        extra={
                            **log_context,
                            "error_message": error_msg,
                            "data_source": data_source,
                            "metric": metric,
                        },
                    )
                    raise ValueError(error_msg)
                if not self._loki_client:
                    error_msg = "Loki 客户端未初始化"
                    self.logger.error(
                        error_msg,
                        extra={
                            **log_context,
                            "error_message": error_msg,
                            "data_source": data_source,
                            "metric": metric,
                        },
                    )
                    raise RuntimeError(error_msg)
            elif data_source != "prometheus":
                error_msg = f"不支持的数据源类型: {data_source}"
                self.logger.error(
                    error_msg,
                    extra={
                        **log_context,
                        "error_message": error_msg,
                        "data_source": data_source,
                        "metric": metric,
                    },
                )
                raise ValueError(error_msg)
        except (ValueError, RuntimeError):
            # 重新抛出异常，但已经记录了日志
            raise
        except Exception as e:
            # 捕获其他未预期的异常
            self.logger.error(
                f"验证数据源时发生未预期的错误: {str(e)}",
                extra={
                    **log_context,
                    "error_message": str(e),
                    "data_source": data_source,
                    "metric": metric,
                },
                exc_info=True,
            )
            raise

        # 计算实际查询时间范围（秒）
        range_seconds = (
            self._parse_time_range(time_range)
            if time_range is not None
            else self._config.query_time_range
        )

        end_time = time.time()
        start_time = end_time - range_seconds

        # 记录查询时间范围（DEBUG 级别包含详细时间戳）
        if self.logger.isEnabledFor(logging.DEBUG):
            self.logger.debug(
                f"查询时间范围: {range_seconds}秒 (从 {time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(start_time))} 到 {time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(end_time))})",
                extra={
                    **log_context,
                    "range_seconds": range_seconds,
                    "start_time": start_time,
                    "end_time": end_time,
                }
            )
        else:
            self.logger.info(
                f"查询时间范围: {range_seconds}秒",
                extra={
                    **log_context,
                    "range_seconds": range_seconds,
                }
            )

        # 根据数据源类型查询时间序列数据
        try:
            if data_source == "prometheus":
                # Prometheus 查询：自动包装计数型指标
                query_expr = self._build_promql(metric)
                self.logger.info(
                    f"执行 Prometheus 查询: {query_expr}",
                    extra={
                        **log_context,
                        "query": query_expr,
                        "original_metric": metric,
                        "data_source": data_source,
                    }
                )
                try:
                    results = self._prometheus_client.query_range(
                        query=query_expr,
                        start=start_time,
                        end=end_time,
                        step=self._config.query_step,
                    )
                except Exception as e:
                    self.logger.error(
                        f"Prometheus 查询失败: {str(e)}",
                        extra={
                            **log_context,
                            "error_message": str(e),
                            "query": query_expr,
                            "data_source": data_source,
                            "metric": metric,
                        },
                        exc_info=True,
                    )
                    raise
                if not results:
                    self.logger.warning(
                        f"Prometheus 查询返回空结果: {query_expr}",
                        extra={
                            **log_context,
                            "query": query_expr,
                            "data_source": data_source,
                            "metric": metric,
                            "status": "no_data",
                        }
                    )
                    return {
                        "metric": metric,
                        "query": query_expr,
                        "data_source": "prometheus",
                        "status": "no_data",
                        "anomalies": [],
                    }
                values = self._aggregate_metric_values(results)
                self.logger.info(
                    f"从 Prometheus 获取到 {len(values)} 个数据点",
                    extra={
                        **log_context,
                        "data_points": len(values),
                        "query": query_expr,
                        "data_source": data_source,
                        "metric": metric,
                    }
                )

            elif data_source == "tempo":
                # Tempo 查询：使用 TraceQL，聚合为时间序列
                query_expr = metric  # Tempo 查询直接使用 metric 参数
                self.logger.info(
                    f"执行 Tempo TraceQL 查询: {query_expr}",
                    extra={
                        **log_context,
                        "query": query_expr,
                        "data_source": data_source,
                        "metric": metric,
                    }
                )
                try:
                    values = self._tempo_client.query_range(
                        query=query_expr,
                        start=start_time,
                        end=end_time,
                        step=self._config.query_step,
                    )
                except Exception as e:
                    self.logger.error(
                        f"Tempo 查询失败: {str(e)}",
                        extra={
                            **log_context,
                            "error_message": str(e),
                            "query": query_expr,
                            "data_source": data_source,
                            "metric": metric,
                        },
                        exc_info=True,
                    )
                    raise
                if len(values) == 0:
                    self.logger.warning(
                        f"Tempo 查询返回空结果: {query_expr}",
                        extra={
                            **log_context,
                            "query": query_expr,
                            "data_source": data_source,
                            "metric": metric,
                            "status": "no_data",
                        }
                    )
                    return {
                        "metric": metric,
                        "query": query_expr,
                        "data_source": "tempo",
                        "status": "no_data",
                        "anomalies": [],
                    }
                self.logger.info(
                    f"从 Tempo 获取到 {len(values)} 个数据点",
                    extra={
                        **log_context,
                        "data_points": len(values),
                        "query": query_expr,
                        "data_source": data_source,
                        "metric": metric,
                    }
                )

            elif data_source == "loki":
                # Loki 查询：使用 LogQL 聚合查询
                query_expr = metric  # Loki 查询直接使用 metric 参数
                self.logger.info(
                    f"执行 Loki LogQL 查询: {query_expr}",
                    extra={
                        **log_context,
                        "query": query_expr,
                        "data_source": data_source,
                        "metric": metric,
                    }
                )
                try:
                    values = self._loki_client.query_range(
                        query=query_expr,
                        start=start_time,
                        end=end_time,
                        step=self._config.query_step,
                    )
                except Exception as e:
                    self.logger.error(
                        f"Loki 查询失败: {str(e)}",
                        extra={
                            **log_context,
                            "error_message": str(e),
                            "query": query_expr,
                            "data_source": data_source,
                            "metric": metric,
                        },
                        exc_info=True,
                    )
                    raise
                if len(values) == 0:
                    self.logger.warning(
                        f"Loki 查询返回空结果: {query_expr}",
                        extra={
                            **log_context,
                            "query": query_expr,
                            "data_source": data_source,
                            "metric": metric,
                            "status": "no_data",
                        }
                    )
                    return {
                        "metric": metric,
                        "query": query_expr,
                        "data_source": "loki",
                        "status": "no_data",
                        "anomalies": [],
                    }
                self.logger.info(
                    f"从 Loki 获取到 {len(values)} 个数据点",
                    extra={
                        **log_context,
                        "data_points": len(values),
                        "query": query_expr,
                        "data_source": data_source,
                        "metric": metric,
                    }
                )
        except Exception as e:
            # 捕获查询执行中的未预期错误
            self.logger.error(
                f"执行数据源查询时发生未预期的错误: {str(e)}",
                extra={
                    **log_context,
                    "error_message": str(e),
                    "data_source": data_source,
                    "metric": metric,
                },
                exc_info=True,
            )
            raise

        # 检查数据点数量
        if len(values) < self._config.min_data_points:
            self.logger.warning(
                f"数据点不足: 当前 {len(values)} 个，需要至少 {self._config.min_data_points} 个",
                extra={
                    **log_context,
                    "data_points": len(values),
                    "min_data_points": self._config.min_data_points,
                    "metric": metric,
                    "data_source": data_source,
                    "status": "insufficient_data",
                }
            )
            return {
                "metric": metric,
                "query": query_expr,
                "data_source": data_source,
                "status": "insufficient_data",
                "data_points": len(values),
                "min_data_points": self._config.min_data_points,
                "anomalies": [],
            }

        # 根据配置的算法类型选择检测方法
        self.logger.info(
            f"开始执行异常检测算法: {self._config.algorithm}, 数据点数量={len(values)}",
            extra={
                **log_context,
                "algorithm": self._config.algorithm,
                "data_points": len(values),
                "min_data_points": self._config.min_data_points,
                "metric": metric,
                "data_source": data_source,
            }
        )
        
        # 根据算法类型选择检测方法
        if self._config.algorithm == "zscore":
            from keep.anomaly_detector.algorithms import get_detector
            detector = get_detector(
                algorithm="zscore",
                zscore_threshold=self._config.zscore_threshold
            )
            detection_result = detector.detect(values, metric)
        elif self._config.algorithm == "isolation_forest":
            from keep.anomaly_detector.algorithms import get_detector
            detector = get_detector(
                algorithm="isolation_forest",
                contamination=self._config.contamination_rate
            )
            detection_result = detector.detect(values, metric)
        elif self._config.algorithm == "both":
            from keep.anomaly_detector.algorithms import get_detector
            detector = get_detector(
                algorithm="both",
                contamination=self._config.contamination_rate,
                zscore_threshold=self._config.zscore_threshold
            )
            detection_result = detector.detect(values, metric)
        else:
            # 默认使用 rate_change 算法（与旧服务兼容）
            detection_result = self._detect_rate_change(metric, values, self._config)
        
        # 记录检测算法的详细信息（用于调试）
        if self.logger.isEnabledFor(logging.DEBUG) and len(values) > 0:
            # 计算一些统计信息用于日志
            values_min = float(np.min(values))
            values_max = float(np.max(values))
            self.logger.debug(
                f"数据统计: 最小值={values_min:.4f}, 最大值={values_max:.4f}, "
                f"均值={detection_result.mean:.4f}, 标准差={detection_result.std:.4f}",
                extra={
                    **log_context,
                    "min_value": values_min,
                    "max_value": values_max,
                    "mean": detection_result.mean,
                    "std": detection_result.std,
                    "metric": metric,
                    "data_source": data_source,
                    "algorithm": detection_result.algorithm,
                }
            )
        
        # 记录检测结果（无论结果如何都记录 INFO 级别日志）
        status = "success" if detection_result.anomaly_count > 0 else "normal"
        self.logger.info(
            f"异常检测完成: 状态={status}, 总数据点={detection_result.total_points}, "
            f"异常数量={detection_result.anomaly_count}, 均值={detection_result.mean:.4f}, "
            f"标准差={detection_result.std:.4f}",
            extra={
                **log_context,
                "status": status,
                "total_points": detection_result.total_points,
                "anomaly_count": detection_result.anomaly_count,
                "mean": detection_result.mean,
                "std": detection_result.std,
                "algorithm": detection_result.algorithm,
                "metric": metric,
                "data_source": data_source,
            }
        )
        
        # 如果发现异常，记录详细信息
        if detection_result.anomaly_count > 0:
            anomalies_summary = [
                {
                    "index": a.index,
                    "value": a.value,
                    "score": a.score,
                }
                for a in detection_result.anomalies[:10]  # 只记录前10个异常点
            ]
            self.logger.warning(
                f"检测到 {detection_result.anomaly_count} 个异常点",
                extra={
                    **log_context,
                    "anomaly_count": detection_result.anomaly_count,
                    "anomalies_summary": anomalies_summary,
                    "metric": metric,
                    "data_source": data_source,
                    "status": status,
                }
            )

        # 自动发送告警：如果异常数量超过阈值，自动向 Keep 平台发送告警
        min_anomaly_count = getattr(
            self.authentication_config, "min_anomaly_count_for_alert", 1
        )
        if detection_result.anomaly_count >= min_anomaly_count:
            try:
                alert = self._build_alert_dto(
                    metric=metric,
                    query_expr=query_expr,
                    data_source=data_source,
                    detection_result=detection_result,
                )
                self._send_alert(alert, log_context)
            except Exception as e:
                # 告警发送失败不应影响检测结果的正常返回
                self.logger.error(
                    f"自动发送告警失败: {e}",
                    extra={
                        **log_context,
                        "error": str(e),
                        "metric": metric,
                        "anomaly_count": detection_result.anomaly_count,
                    },
                    exc_info=True,
                )

        return {
            "metric": metric,
            "query": query_expr,
            "data_source": data_source,
            "status": "success"
            if detection_result.anomaly_count > 0
            else "normal",
            "total_points": detection_result.total_points,
            "anomaly_count": detection_result.anomaly_count,
            "mean": detection_result.mean,
            "std": detection_result.std,
            "algorithm": detection_result.algorithm,
            "anomalies": [
                {
                    "index": a.index,
                    "value": a.value,
                    "score": a.score,
                    "method": a.method,
                    "details": a.details,
                }
                for a in detection_result.anomalies
            ],
        }

    @staticmethod
    def _parse_time_range(value: str) -> int:
        """解析类似 '30m'、'1h'、'2d' 的时长字符串为秒数。"""
        import re

        if not value:
            return 3600

        match = re.match(r"(\d+)([smhd])$", value.strip().lower())
        if not match:
            return 3600

        amount, unit = match.groups()
        amount = int(amount)

        factor = {"s": 1, "m": 60, "h": 3600, "d": 86400}.get(unit, 60)
        return amount * factor

    def _run_batch_detection(
        self,
        time_range: Optional[str],
        log_context: Dict[str, Any],
    ) -> Dict[str, Any]:
        """
        批量检测模式：

        - 使用 include_metrics/exclude_metrics 和最大指标数量上限，从 Prometheus 自动发现候选指标列表
        - 对每个候选指标执行一次 Prometheus 查询 + 异常检测 + 自动告警
        - 返回本次批量检测的汇总信息和每个指标的检测结果摘要
        """
        # 仅支持 Prometheus，且必须有配置与客户端
        if not self._config or not self._prometheus_client:
            error_msg = "批量检测模式下 Provider 配置尚未正确初始化，无法访问 Prometheus。"
            self.logger.error(
                error_msg,
                extra={
                    **log_context,
                    "error_message": error_msg,
                },
            )
            raise RuntimeError(error_msg)

        include_patterns = self._config.include_metrics or []
        exclude_patterns = self._config.exclude_metrics or []
        max_metrics = self._config.max_metrics or 0
        if max_metrics <= 0:
            max_metrics = 100

        # 记录批量检测配置
        self.logger.info(
            "开始批量异常检测: include_metrics=%s, exclude_metrics=%s, max_metrics=%d",
            include_patterns,
            exclude_patterns,
            max_metrics,
            extra={
                **log_context,
                "mode": "multi",
                "include_metrics": include_patterns,
                "exclude_metrics": exclude_patterns,
                "max_metrics": max_metrics,
            },
        )

        # 从 Prometheus 自动发现候选指标
        try:
            candidate_metrics = self._prometheus_client.list_metrics(
                include_patterns=include_patterns,
                exclude_patterns=exclude_patterns,
                max_metrics=max_metrics,
            )
        except Exception as e:
            error_msg = f"批量检测模式下获取 Prometheus 指标列表失败: {e}"
            self.logger.error(
                error_msg,
                extra={
                    **log_context,
                    "error_message": str(e),
                },
                exc_info=True,
            )
            raise RuntimeError(error_msg) from e

        if not candidate_metrics:
            self.logger.warning(
                "批量检测模式未发现任何候选指标，请检查 include_metrics/exclude_metrics 配置",
                extra={
                    **log_context,
                    "mode": "multi",
                    "metrics_checked": 0,
                },
            )
            return {
                "mode": "multi",
                "data_source": "prometheus",
                "metrics_checked": 0,
                "alerts_sent": 0,
                "results": [],
            }

        # 计算查询时间范围
        range_seconds = (
            self._parse_time_range(time_range)
            if time_range is not None
            else self._config.query_time_range
        )
        end_time = time.time()
        start_time = end_time - range_seconds

        results: List[Dict[str, Any]] = []
        alerts_sent = 0

        for metric_name in candidate_metrics:
            try:
                query_expr = self._build_promql(metric_name)
                self.logger.info(
                    "批量检测模式执行 Prometheus 查询: %s",
                    query_expr,
                    extra={
                        **log_context,
                        "mode": "multi",
                        "metric": metric_name,
                        "query": query_expr,
                    },
                )
                prom_results = self._prometheus_client.query_range(
                    query=query_expr,
                    start=start_time,
                    end=end_time,
                    step=self._config.query_step,
                )
            except Exception as e:
                self.logger.error(
                    "批量检测模式下 Prometheus 查询失败: %s",
                    str(e),
                    extra={
                        **log_context,
                        "mode": "multi",
                        "metric": metric_name,
                        "error_message": str(e),
                    },
                    exc_info=True,
                )
                # 将错误作为该指标的结果返回，便于排查
                results.append(
                    {
                        "metric": metric_name,
                        "query": query_expr,
                        "data_source": "prometheus",
                        "status": "error",
                        "error": str(e),
                        "anomalies": [],
                    }
                )
                continue

            if not prom_results:
                self.logger.info(
                    "批量检测模式下指标无数据: %s",
                    query_expr,
                    extra={
                        **log_context,
                        "mode": "multi",
                        "metric": metric_name,
                        "query": query_expr,
                        "status": "no_data",
                    },
                )
                results.append(
                    {
                        "metric": metric_name,
                        "query": query_expr,
                        "data_source": "prometheus",
                        "status": "no_data",
                        "anomalies": [],
                    }
                )
                continue

            values = self._aggregate_metric_values(prom_results)
            if len(values) < self._config.min_data_points:
                self.logger.info(
                    "批量检测模式下数据点不足: metric=%s, data_points=%d, min_data_points=%d",
                    metric_name,
                    len(values),
                    self._config.min_data_points,
                    extra={
                        **log_context,
                        "mode": "multi",
                        "metric": metric_name,
                        "data_points": len(values),
                        "min_data_points": self._config.min_data_points,
                        "status": "insufficient_data",
                    },
                )
                results.append(
                    {
                        "metric": metric_name,
                        "query": query_expr,
                        "data_source": "prometheus",
                        "status": "insufficient_data",
                        "data_points": len(values),
                        "min_data_points": self._config.min_data_points,
                        "anomalies": [],
                    }
                )
                continue

            # 执行异常检测（与单指标模式保持一致）
            self.logger.info(
                "批量检测模式下执行异常检测: metric=%s, algorithm=%s, data_points=%d",
                metric_name,
                self._config.algorithm,
                len(values),
                extra={
                    **log_context,
                    "mode": "multi",
                    "metric": metric_name,
                    "algorithm": self._config.algorithm,
                    "data_points": len(values),
                },
            )

            if self._config.algorithm == "zscore":
                from keep.anomaly_detector.algorithms import get_detector

                detector = get_detector(
                    algorithm="zscore",
                    zscore_threshold=self._config.zscore_threshold,
                )
                detection_result = detector.detect(values, metric_name)
            elif self._config.algorithm == "isolation_forest":
                from keep.anomaly_detector.algorithms import get_detector

                detector = get_detector(
                    algorithm="isolation_forest",
                    contamination=self._config.contamination_rate,
                )
                detection_result = detector.detect(values, metric_name)
            elif self._config.algorithm == "both":
                from keep.anomaly_detector.algorithms import get_detector

                detector = get_detector(
                    algorithm="both",
                    contamination=self._config.contamination_rate,
                    zscore_threshold=self._config.zscore_threshold,
                )
                detection_result = detector.detect(values, metric_name)
            else:
                detection_result = self._detect_rate_change(
                    metric_name, values, self._config
                )

            status = "success" if detection_result.anomaly_count > 0 else "normal"

            # 根据异常数量阈值自动发送告警
            min_anomaly_count = getattr(
                self.authentication_config, "min_anomaly_count_for_alert", 1
            )
            if detection_result.anomaly_count >= min_anomaly_count:
                try:
                    alert = self._build_alert_dto(
                        metric=metric_name,
                        query_expr=query_expr,
                        data_source="prometheus",
                        detection_result=detection_result,
                    )
                    self._send_alert(alert, log_context)
                    alerts_sent += 1
                except Exception as e:
                    self.logger.error(
                        "批量检测模式下自动发送告警失败: %s",
                        str(e),
                        extra={
                            **log_context,
                            "mode": "multi",
                            "metric": metric_name,
                            "error": str(e),
                        },
                        exc_info=True,
                    )

            results.append(
                {
                    "metric": metric_name,
                    "query": query_expr,
                    "data_source": "prometheus",
                    "status": status,
                    "total_points": detection_result.total_points,
                    "anomaly_count": detection_result.anomaly_count,
                    "mean": detection_result.mean,
                    "std": detection_result.std,
                    "algorithm": detection_result.algorithm,
                    "anomalies": [
                        {
                            "index": a.index,
                            "value": a.value,
                            "score": a.score,
                            "method": a.method,
                            "details": a.details,
                        }
                        for a in detection_result.anomalies
                    ],
                }
            )

        self.logger.info(
            "批量异常检测完成: 指标数量=%d, 已发送告警数量=%d",
            len(results),
            alerts_sent,
            extra={
                **log_context,
                "mode": "multi",
                "metrics_checked": len(results),
                "alerts_sent": alerts_sent,
            },
        )

        return {
            "mode": "multi",
            "data_source": "prometheus",
            "metrics_checked": len(results),
            "alerts_sent": alerts_sent,
            "results": results,
        }

    @staticmethod
    def _build_promql(metric: str) -> str:
        """根据传入的 metric 构造最终 PromQL。

        - 如果已经包含操作符/括号，认为是完整 PromQL，直接返回
        - 否则根据名称后缀判断是否为计数型指标，并自动包装 rate()
        """
        import re

        # 简单启发式：含操作符 / 括号 / 花括号的视为完整 PromQL
        if re.search(r"[(){}+\\/*-]", metric):
            return metric

        is_counter = metric.endswith("_count") or metric.endswith(
            "_sum"
        ) or metric.endswith("_total") or metric.endswith("_bucket")

        if is_counter:
            rate_window = "3m"
            return f"rate({metric}[{rate_window}])"

        return metric

    @staticmethod
    def _aggregate_metric_values(results: List[Dict[str, Any]]) -> np.ndarray:
        """将 Prometheus 返回的多条时间序列按时间戳聚合成一条数值序列。"""
        from collections import defaultdict

        time_buckets: Dict[float, float] = defaultdict(float)

        for series in results:
            values = series.get("values", [])
            for timestamp, value in values:
                try:
                    time_buckets[float(timestamp)] += float(value)
                except (ValueError, TypeError):
                    continue

        if not time_buckets:
            return np.array([])

        sorted_items = sorted(time_buckets.items())
        return np.array([v for _, v in sorted_items])

    def _detect_rate_change(
        self,
        metric_name: str,
        values: np.ndarray,
        config: AnomalyDetectorConfig,
    ) -> DetectionResult:
        """
        使用“环比变化 + 基线”方式进行异常检测。

        该实现等价于原 `AnomalyDetectorService._detect_anomalies` 中
        针对 metrics 的改进逻辑，只是封装成独立函数供 Provider 调用。
        """
        if len(values) == 0:
            return DetectionResult(
                metric_name=metric_name,
                anomalies=[],
                total_points=0,
                anomaly_count=0,
                mean=0.0,
                std=0.0,
                algorithm="rate_change",
            )

        # Startup exclusion: drop the first 30% of points
        startup_exclusion_ratio = 0.3
        startup_exclusion_size = max(1, int(len(values) * startup_exclusion_ratio))
        stable_history = values[startup_exclusion_size:]

        if len(stable_history) < config.min_data_points + 10:
            # Not enough data after startup exclusion
            return DetectionResult(
                metric_name=metric_name,
                anomalies=[],
                total_points=len(values),
                anomaly_count=0,
                mean=float(np.mean(stable_history))
                if len(stable_history) > 0
                else 0.0,
                std=float(np.std(stable_history))
                if len(stable_history) > 0
                else 0.0,
                algorithm="rate_change",
            )

        # Baseline: first 80% of stable history, but keep at least 10 points
        baseline_size = max(
            config.min_data_points, int(len(stable_history) * 0.8)
        )
        baseline_size = min(baseline_size, len(stable_history) - 10)

        baseline_raw = stable_history[:baseline_size]
        detection_window = stable_history[baseline_size:]

        # Robust baseline: median / mean; special handling for zero baseline
        baseline_median = float(np.median(baseline_raw))
        baseline_mean = float(np.mean(baseline_raw))
        baseline_value = max(baseline_median, baseline_mean * 0.8)

        anomalies: List[AnomalyResult] = []

        if baseline_value == 0:
            # Any non-zero value is an anomaly if baseline is effectively zero
            for i, value in enumerate(detection_window):
                if value > 0:
                    anomalies.append(
                        AnomalyResult(
                            index=baseline_size + i,
                            value=float(value),
                            is_anomaly=True,
                            score=999.0,
                            method="rate_change",
                            details={
                                "baseline_median": baseline_median,
                                "baseline_mean": baseline_mean,
                                "baseline_value": baseline_value,
                                "rate_change_threshold": config.rate_change_threshold,
                                "startup_exclusion": startup_exclusion_size,
                            },
                        )
                    )
        else:
            threshold_value = baseline_value * (1 + config.rate_change_threshold)
            for i, value in enumerate(detection_window):
                if value > threshold_value:
                    change_ratio = float((value - baseline_value) / baseline_value)
                    anomalies.append(
                        AnomalyResult(
                            index=baseline_size + i,
                            value=float(value),
                            is_anomaly=True,
                            score=change_ratio,
                            method="rate_change",
                            details={
                                "baseline_median": baseline_median,
                                "baseline_mean": baseline_mean,
                                "baseline_value": baseline_value,
                                "rate_change_threshold": config.rate_change_threshold,
                                "startup_exclusion": startup_exclusion_size,
                            },
                        )
                    )

        return DetectionResult(
            metric_name=metric_name,
            anomalies=anomalies,
            total_points=len(values),
            anomaly_count=len(anomalies),
            mean=float(np.mean(stable_history)),
            std=float(np.std(stable_history)),
            algorithm="rate_change",
        )

    def _generate_fingerprint(
        self, metric_name: str, labels: Dict[str, Any]
    ) -> str:
        """
        生成告警 fingerprint，用于 Keep 平台的告警去重。

        Args:
            metric_name: 指标名称
            labels: 告警标签字典

        Returns:
            告警 fingerprint（SHA-256 哈希值）
        """
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

    def _build_alert_dto(
        self,
        metric: str,
        query_expr: str,
        data_source: str,
        detection_result: DetectionResult,
    ) -> AlertDto:
        """
        构建包含完整异常检测信息的 AlertDto 对象。

        Args:
            metric: 指标名称或查询表达式
            query_expr: 实际执行的查询语句
            data_source: 数据源类型
            detection_result: 异常检测结果

        Returns:
            构建好的 AlertDto 对象
        """
        # 构建告警名称
        alert_name = f"异常检测告警: {metric}"

        # 构建告警描述
        description_parts = [
            f"指标 {metric} 在检测时间范围内发现 {detection_result.anomaly_count} 个异常点。",
            f"检测算法: {detection_result.algorithm}",
            f"数据源: {data_source}",
            f"总数据点: {detection_result.total_points}",
            f"统计信息: 均值={detection_result.mean:.4f}, 标准差={detection_result.std:.4f}",
        ]

        # 添加异常点详情（前5个）
        if detection_result.anomalies:
            description_parts.append("\n异常点详情（前5个）:")
            for i, anomaly in enumerate(detection_result.anomalies[:5], 1):
                description_parts.append(
                    f"  {i}. 索引={anomaly.index}, 数值={anomaly.value:.4f}, "
                    f"评分={anomaly.score:.4f}, 方法={anomaly.method}"
                )

        description = "\n".join(description_parts)

        # 构建告警标签
        labels = {
            "metric": metric,
            "data_source": data_source,
            "algorithm": detection_result.algorithm,
            "anomaly_count": str(detection_result.anomaly_count),
            "total_points": str(detection_result.total_points),
        }

        # 生成 fingerprint
        fingerprint = self._generate_fingerprint(metric, labels)

        # 确定告警严重程度（根据异常数量）
        if detection_result.anomaly_count >= 10:
            severity = AlertSeverity.CRITICAL
        elif detection_result.anomaly_count >= 5:
            severity = AlertSeverity.WARNING
        else:
            severity = AlertSeverity.INFO

        # 根据数据源类型设置 source，用于显示正确的图标
        # 对于 Prometheus 数据源，使用 "prometheus" 以显示 Prometheus 图标
        # 对于 Tempo/Loki，使用对应的数据源名称
        source_value = [data_source] if data_source in ["prometheus", "tempo", "loki"] else ["anomaly_detector"]

        # 构建 AlertDto
        alert = AlertDto(
            id=str(uuid.uuid4()),
            name=alert_name,
            status=AlertStatus.FIRING,
            severity=severity,
            lastReceived=datetime.now(timezone.utc).isoformat(),
            message=f"检测到 {detection_result.anomaly_count} 个异常点",
            description=description,
            labels=labels,
            fingerprint=fingerprint,
            source=source_value,
            environment="production",  # 可以从配置获取
            providerId=self.provider_id,
            providerType="anomaly_detector",
        )

        return alert

    def _send_alert(self, alert: AlertDto, log_context: Dict[str, Any]) -> None:
        """
        向 Keep 平台发送告警。

        Args:
            alert: 要发送的告警对象
            log_context: 日志上下文信息
        """
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

            self.logger.info(
                f"自动告警发送成功: {alert.name}, fingerprint={alert.fingerprint}",
                extra={
                    **log_context,
                    "alert_name": alert.name,
                    "alert_fingerprint": alert.fingerprint,
                    "anomaly_count": alert.labels.get("anomaly_count"),
                    "metric": alert.labels.get("metric"),
                },
            )
        except Exception as e:
            # 告警发送失败不应影响检测结果的正常返回
            self.logger.error(
                f"自动发送告警失败: {e}",
                extra={
                    **log_context,
                    "error": str(e),
                    "alert_name": alert.name,
                    "alert_fingerprint": alert.fingerprint,
                    "metric": alert.labels.get("metric"),
                },
                exc_info=True,
            )
            # 不抛出异常，确保检测结果正常返回


