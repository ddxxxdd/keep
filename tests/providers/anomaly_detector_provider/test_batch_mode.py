"""
测试批量检测模式（自动发现多条 Prometheus 指标并逐个执行异常检测）。
"""

import pytest
import numpy as np
from unittest.mock import patch

from keep.anomaly_detector.algorithms import DetectionResult
from keep.anomaly_detector.config import AnomalyDetectorConfig, PrometheusConfig
from keep.contextmanager.contextmanager import ContextManager
from keep.providers.anomaly_detector_provider.anomaly_detector_provider import (
    AnomalyDetectorProvider,
)
from keep.providers.models.provider_config import ProviderConfig
from keep.providers.anomaly_detector_provider.clients.prometheus_client import (
    PrometheusClient,
)


def test_prometheus_client_list_metrics_respects_include_exclude_and_limit():
    """测试 PrometheusClient.list_metrics 能正确应用包含/排除规则和数量上限。"""
    config = AnomalyDetectorConfig(
        prometheus=PrometheusConfig(url="http://localhost:9090"),
    )
    client = PrometheusClient(config)

    # 模拟 Prometheus 返回的指标列表
    fake_response = {
        "status": "success",
        "data": [
            "keep_http_requests_total",
            "keep_http_server_duration_seconds_bucket",
            "go_goroutines",
            "process_cpu_seconds_total",
        ],
    }

    with patch.object(
        client, "_make_request", return_value=fake_response
    ) as mock_make_request:
        metrics = client.list_metrics(
            include_patterns=[r"^keep_http_"],
            exclude_patterns=[r"^keep_http_server_duration_seconds_bucket$"],
            max_metrics=10,
        )

        mock_make_request.assert_called_once()
        # 只应返回以 keep_http_ 开头但不包含被排除的指标
        assert metrics == ["keep_http_requests_total"]


@pytest.fixture
def context_manager():
    """创建测试用的 ContextManager。"""
    return ContextManager(tenant_id="test_tenant", workflow_id="test_workflow")


@pytest.fixture
def provider_config_batch():
    """创建启用批量检测模式的 Provider 配置。"""
    return ProviderConfig(
        authentication={
            "prometheus_url": "http://localhost:9090",
            "algorithm": "zscore",
            "min_data_points": 5,
            "history_size": 100,
            "query_range_seconds": 300,
            "query_step": "60s",
            "rate_change_threshold": 0.5,
            "include_metrics": [r"^metric_"],
            "exclude_metrics": [r"^metric_excluded_"],
            "max_metrics": 3,
            "min_anomaly_count_for_alert": 1,
        }
    )


@pytest.fixture
def provider_batch(context_manager, provider_config_batch):
    """创建支持批量检测模式的 Provider 实例。"""
    return AnomalyDetectorProvider(
        context_manager=context_manager,
        provider_id="test-anomaly-detector-batch",
        config=provider_config_batch,
    )


def _fake_detection_result(metric_name: str, values: np.ndarray) -> DetectionResult:
    """构造一个简单的 DetectionResult，便于在测试中控制异常数量。"""
    # 约定：metric_0 视为有异常，其他视为正常
    if metric_name == "metric_0":
        anomaly_count = 2
    else:
        anomaly_count = 0

    return DetectionResult(
        metric_name=metric_name,
        anomalies=[],
        total_points=len(values),
        anomaly_count=anomaly_count,
        mean=float(np.mean(values)) if len(values) > 0 else 0.0,
        std=float(np.std(values)) if len(values) > 0 else 0.0,
        algorithm="zscore",
    )


def test_batch_mode_summary_and_alerts_count(provider_batch):
    """
    测试批量检测模式的汇总结果结构：
    - 返回 mode="multi"
    - metrics_checked 为实际检测的指标数量
    - alerts_sent 为触发自动告警的指标数量
    """
    provider = provider_batch

    # 模拟 Prometheus 返回三个候选指标
    with patch.object(
        provider._prometheus_client,
        "list_metrics",
        return_value=["metric_0", "metric_1", "metric_2"],
    ):
        # 模拟每个指标的 Prometheus 查询结果：简单的等差序列
        def fake_query_range(query: str, start: float, end: float, step: str):
            return [
                {
                    "metric": {"__name__": query},
                    "values": [[start + i * 60, str(10.0 + i)] for i in range(10)],
                }
            ]

        with patch.object(
            provider._prometheus_client,
            "query_range",
            side_effect=fake_query_range,
        ), patch.object(
            provider,
            "_detect_rate_change",
            side_effect=lambda metric_name, values, config: _fake_detection_result(
                metric_name, values
            ),
        ), patch.object(
            provider, "_send_alert"
        ) as mock_send_alert:
            # 通过 _query 触发批量模式（metric="__all__"）
            result = provider._query(metric="__all__", data_source="prometheus")

            assert result["mode"] == "multi"
            assert result["data_source"] == "prometheus"
            assert result["metrics_checked"] == 3
            # 只有 metric_0 产生异常并触发告警
            assert result["alerts_sent"] == 1
            assert len(result["results"]) == 3

            # 验证 _send_alert 被调用了一次
            assert mock_send_alert.call_count == 1


