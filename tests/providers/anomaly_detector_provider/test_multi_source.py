"""
测试 Anomaly Detector Provider 的多数据源切换和集成场景。
"""

import pytest
from unittest.mock import Mock, patch
import numpy as np

from keep.contextmanager.contextmanager import ContextManager
from keep.providers.anomaly_detector_provider.anomaly_detector_provider import (
    AnomalyDetectorProvider,
)
from keep.providers.models.provider_config import ProviderConfig


@pytest.fixture
def context_manager():
    """创建测试用的 ContextManager。"""
    return ContextManager(tenant_id="test_tenant", workflow_id="test_workflow")


@pytest.fixture
def provider_config_all_sources():
    """创建启用所有数据源的 Provider 配置。"""
    return ProviderConfig(
        authentication={
            "prometheus_url": "http://localhost:9090",
            "prometheus_username": "",
            "prometheus_password": "",
            "prometheus_verify_ssl": True,
            "algorithm": "both",
            "min_data_points": 20,
            "history_size": 1000,
            "query_range_seconds": 3600,
            "query_step": "60s",
            "rate_change_threshold": 0.5,
            "tempo_url": "http://localhost:3200",
            "tempo_enabled": True,
            "loki_url": "http://localhost:3100",
            "loki_enabled": True,
        }
    )


@pytest.fixture
def provider_all_sources(context_manager, provider_config_all_sources):
    """创建启用所有数据源的 Provider 实例。"""
    return AnomalyDetectorProvider(
        context_manager=context_manager,
        provider_id="test-anomaly-detector-all",
        config=provider_config_all_sources,
    )


def test_switch_between_prometheus_and_tempo(provider_all_sources):
    """测试在同一 Provider 实例中切换 Prometheus 和 Tempo 数据源。"""
    # Mock Prometheus 查询
    prometheus_results = [
        {
            "metric": {"job": "test"},
            "values": [[1000 + i * 60, str(10.0 + i * 0.5)] for i in range(30)],
        }
    ]

    # Mock Tempo 查询
    tempo_values = np.array([10.0] * 30)

    with patch.object(
        provider_all_sources._prometheus_client,
        "query_range",
        return_value=prometheus_results,
    ), patch.object(
        provider_all_sources._tempo_client, "query_range", return_value=tempo_values
    ):
        # 先查询 Prometheus
        result_prom = provider_all_sources._query(
            metric="test_metric", data_source="prometheus"
        )
        assert result_prom["data_source"] == "prometheus"

        # 再查询 Tempo
        result_tempo = provider_all_sources._query(
            metric='{ .service_name = "frontend" }', data_source="tempo"
        )
        assert result_tempo["data_source"] == "tempo"


def test_switch_between_all_three_sources(provider_all_sources):
    """测试在同一 Provider 实例中切换 Prometheus、Tempo 和 Loki 三种数据源。"""
    prometheus_results = [
        {
            "metric": {"job": "test"},
            "values": [[1000 + i * 60, str(10.0 + i * 0.5)] for i in range(30)],
        }
    ]
    tempo_values = np.array([10.0] * 30)
    loki_values = np.array([45.0] * 30)

    with patch.object(
        provider_all_sources._prometheus_client,
        "query_range",
        return_value=prometheus_results,
    ), patch.object(
        provider_all_sources._tempo_client, "query_range", return_value=tempo_values
    ), patch.object(
        provider_all_sources._loki_client, "query_range", return_value=loki_values
    ):
        # 查询 Prometheus
        result_prom = provider_all_sources._query(
            metric="test_metric", data_source="prometheus"
        )
        assert result_prom["data_source"] == "prometheus"

        # 查询 Tempo
        result_tempo = provider_all_sources._query(
            metric='{ .service_name = "frontend" }', data_source="tempo"
        )
        assert result_tempo["data_source"] == "tempo"

        # 查询 Loki
        result_loki = provider_all_sources._query(
            metric='sum(rate({job="varlogs"}[5m]))', data_source="loki"
        )
        assert result_loki["data_source"] == "loki"


def test_unified_anomaly_detection_algorithm(provider_all_sources):
    """测试所有数据源使用统一的异常检测算法。"""
    # 创建包含明显异常的数据序列
    normal_data = np.array([10.0] * 20)
    anomaly_data = np.array([50.0] * 5)  # 明显异常
    combined_data = np.concatenate([normal_data, anomaly_data])

    prometheus_results = [
        {
            "metric": {"job": "test"},
            "values": [
                [1000 + i * 60, str(combined_data[i])] for i in range(len(combined_data))
            ],
        }
    ]
    tempo_values = combined_data
    loki_values = combined_data

    with patch.object(
        provider_all_sources._prometheus_client,
        "query_range",
        return_value=prometheus_results,
    ), patch.object(
        provider_all_sources._tempo_client, "query_range", return_value=tempo_values
    ), patch.object(
        provider_all_sources._loki_client, "query_range", return_value=loki_values
    ):
        # 所有数据源应该检测到相同的异常模式
        result_prom = provider_all_sources._query(
            metric="test_metric", data_source="prometheus"
        )
        result_tempo = provider_all_sources._query(
            metric='{ .service_name = "frontend" }', data_source="tempo"
        )
        result_loki = provider_all_sources._query(
            metric='sum(rate({job="varlogs"}[5m]))', data_source="loki"
        )

        # 验证所有结果都包含异常检测信息
        for result in [result_prom, result_tempo, result_loki]:
            assert "anomaly_count" in result
            assert "anomalies" in result
            assert "algorithm" in result
            # 由于数据相同，异常数量应该一致
            assert result["anomaly_count"] > 0


def test_consistent_result_structure_across_sources(provider_all_sources):
    """测试所有数据源返回的结果结构一致。"""
    prometheus_results = [
        {
            "metric": {"job": "test"},
            "values": [[1000 + i * 60, str(10.0 + i * 0.5)] for i in range(30)],
        }
    ]
    tempo_values = np.array([10.0] * 30)
    loki_values = np.array([45.0] * 30)

    with patch.object(
        provider_all_sources._prometheus_client,
        "query_range",
        return_value=prometheus_results,
    ), patch.object(
        provider_all_sources._tempo_client, "query_range", return_value=tempo_values
    ), patch.object(
        provider_all_sources._loki_client, "query_range", return_value=loki_values
    ):
        result_prom = provider_all_sources._query(
            metric="test_metric", data_source="prometheus"
        )
        result_tempo = provider_all_sources._query(
            metric='{ .service_name = "frontend" }', data_source="tempo"
        )
        result_loki = provider_all_sources._query(
            metric='sum(rate({job="varlogs"}[5m]))', data_source="loki"
        )

        # 验证所有结果包含相同的字段结构
        expected_fields = [
            "metric",
            "query",
            "data_source",
            "status",
            "total_points",
            "anomaly_count",
            "mean",
            "std",
            "algorithm",
            "anomalies",
        ]

        for result in [result_prom, result_tempo, result_loki]:
            for field in expected_fields:
                assert field in result, f"结果缺少字段: {field} (data_source: {result.get('data_source')})"

