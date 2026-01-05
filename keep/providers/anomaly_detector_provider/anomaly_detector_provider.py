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

注意：这里不再启动独立的后台线程，也不会主动向 Keep API 发送告警，
而是通过 ``_query`` 返回检测结果，由工作流决定后续动作（通知、创建事件等）。
"""



from __future__ import annotations

import dataclasses
import logging
import time
from typing import Any, Dict, List, Optional

import numpy as np
import pydantic

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
        
        try:
            self.authentication_config = AnomalyDetectorProviderAuthConfig(
                **self.config.authentication
            )
        except Exception as e:
            error_msg = (
                f"Failed to create AnomalyDetectorProviderAuthConfig: {e}. "
                f"Authentication config: {self.config.authentication}. "
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
            max_metrics=0,  # Provider 场景下不限制全局监控指标数量
            contamination_rate=0.005,  # 与旧服务配置一致（ANOMALY_DETECTOR_CONTAMINATION=0.005）
            zscore_threshold=2.0,  # 与旧服务配置一致（ANOMALY_DETECTOR_ZSCORE_THRESHOLD=2.0）
            history_size=self.authentication_config.history_size,
            query_time_range=self.authentication_config.query_range_seconds,
            query_step=self.authentication_config.query_step,
            include_metrics=[],
            exclude_metrics=[],
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
            self.logger.error(error_msg)
            raise RuntimeError(error_msg)

        # 确定数据源类型（默认为 prometheus）
        data_source = data_source or "prometheus"

        # 添加 INFO 日志：开始执行异常检测
        self.logger.info(
            f"开始执行异常检测: metric={metric}, data_source={data_source}, time_range={time_range or 'default'}",
            extra={
                "metric": metric,
                "data_source": data_source,
                "time_range": time_range,
            }
        )

        # 验证数据源是否已启用
        if data_source == "tempo":
            if not self.authentication_config.tempo_enabled:
                raise ValueError("数据源 'tempo' 未在 Provider 配置中启用")
            if not self._tempo_client:
                raise RuntimeError("Tempo 客户端未初始化")
        elif data_source == "loki":
            if not self.authentication_config.loki_enabled:
                raise ValueError("数据源 'loki' 未在 Provider 配置中启用")
            if not self._loki_client:
                raise RuntimeError("Loki 客户端未初始化")
        elif data_source != "prometheus":
            raise ValueError(f"不支持的数据源类型: {data_source}")

        # 计算实际查询时间范围（秒）
        range_seconds = (
            self._parse_time_range(time_range)
            if time_range is not None
            else self._config.query_time_range
        )

        end_time = time.time()
        start_time = end_time - range_seconds

        self.logger.info(
            f"查询时间范围: {range_seconds}秒 (从 {time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(start_time))} 到 {time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(end_time))})",
            extra={
                "range_seconds": range_seconds,
                "start_time": start_time,
                "end_time": end_time,
            }
        )

        # 根据数据源类型查询时间序列数据
        if data_source == "prometheus":
            # Prometheus 查询：自动包装计数型指标
            query_expr = self._build_promql(metric)
            self.logger.info(
                f"执行 Prometheus 查询: {query_expr}",
                extra={"query": query_expr, "original_metric": metric}
            )
            results = self._prometheus_client.query_range(
                query=query_expr,
                start=start_time,
                end=end_time,
                step=self._config.query_step,
            )
            if not results:
                self.logger.warning(
                    f"Prometheus 查询返回空结果: {query_expr}",
                    extra={"query": query_expr}
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
                extra={"data_points": len(values), "query": query_expr}
            )

        elif data_source == "tempo":
            # Tempo 查询：使用 TraceQL，聚合为时间序列
            query_expr = metric  # Tempo 查询直接使用 metric 参数
            self.logger.info(
                f"执行 Tempo TraceQL 查询: {query_expr}",
                extra={"query": query_expr}
            )
            values = self._tempo_client.query_range(
                query=query_expr,
                start=start_time,
                end=end_time,
                step=self._config.query_step,
            )
            if len(values) == 0:
                self.logger.warning(
                    f"Tempo 查询返回空结果: {query_expr}",
                    extra={"query": query_expr}
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
                extra={"data_points": len(values), "query": query_expr}
            )

        elif data_source == "loki":
            # Loki 查询：使用 LogQL 聚合查询
            query_expr = metric  # Loki 查询直接使用 metric 参数
            self.logger.info(
                f"执行 Loki LogQL 查询: {query_expr}",
                extra={"query": query_expr}
            )
            values = self._loki_client.query_range(
                query=query_expr,
                start=start_time,
                end=end_time,
                step=self._config.query_step,
            )
            if len(values) == 0:
                self.logger.warning(
                    f"Loki 查询返回空结果: {query_expr}",
                    extra={"query": query_expr}
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
                extra={"data_points": len(values), "query": query_expr}
            )

        # 检查数据点数量
        if len(values) < self._config.min_data_points:
            self.logger.warning(
                f"数据点不足: 当前 {len(values)} 个，需要至少 {self._config.min_data_points} 个",
                extra={
                    "data_points": len(values),
                    "min_data_points": self._config.min_data_points,
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
                "algorithm": self._config.algorithm,
                "data_points": len(values),
                "min_data_points": self._config.min_data_points,
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
        if hasattr(detection_result, 'details') or len(values) > 0:
            # 计算一些统计信息用于日志
            values_min = float(np.min(values)) if len(values) > 0 else 0.0
            values_max = float(np.max(values)) if len(values) > 0 else 0.0
            self.logger.debug(
                f"数据统计: 最小值={values_min:.4f}, 最大值={values_max:.4f}, "
                f"均值={detection_result.mean:.4f}, 标准差={detection_result.std:.4f}",
                extra={
                    "min": values_min,
                    "max": values_max,
                    "mean": detection_result.mean,
                    "std": detection_result.std,
                }
            )
        
        # 记录检测结果
        status = "success" if detection_result.anomaly_count > 0 else "normal"
        self.logger.info(
            f"异常检测完成: 状态={status}, 总数据点={detection_result.total_points}, "
            f"异常数量={detection_result.anomaly_count}, 均值={detection_result.mean:.4f}, "
            f"标准差={detection_result.std:.4f}",
            extra={
                "status": status,
                "total_points": detection_result.total_points,
                "anomaly_count": detection_result.anomaly_count,
                "mean": detection_result.mean,
                "std": detection_result.std,
                "algorithm": detection_result.algorithm,
            }
        )
        
        # 如果发现异常，记录详细信息
        if detection_result.anomaly_count > 0:
            self.logger.warning(
                f"检测到 {detection_result.anomaly_count} 个异常点",
                extra={
                    "anomaly_count": detection_result.anomaly_count,
                    "anomalies": [
                        {
                            "index": a.index,
                            "value": a.value,
                            "score": a.score,
                        }
                        for a in detection_result.anomalies[:10]  # 只记录前10个异常点
                    ],
                }
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

        match = re.match(r"(\\d+)([smhd])$", value.strip().lower())
        if not match:
            return 3600

        amount, unit = match.groups()
        amount = int(amount)

        factor = {"s": 1, "m": 60, "h": 3600, "d": 86400}.get(unit, 60)
        return amount * factor

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



