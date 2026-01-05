"""
测试 Anomaly Detector Provider 的 Tempo traces 异常检测功能。
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
def provider_config_with_tempo():
    """创建启用 Tempo 的 Provider 配置。"""
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
            "tempo_enabled": True,  # 启用 Tempo
            "loki_url": "http://localhost:3100",
            "loki_enabled": False,
        }
    )


@pytest.fixture
def provider_with_tempo(context_manager, provider_config_with_tempo):
    """创建启用 Tempo 的 Provider 实例。"""
    return AnomalyDetectorProvider(
        context_manager=context_manager,
        provider_id="test-anomaly-detector-tempo",
        config=provider_config_with_tempo,
    )


def test_query_tempo_success(provider_with_tempo):
    """测试 Tempo 查询成功的情况。"""
    # Mock TempoClient 返回的时间序列数据（numpy array）
    mock_values = np.array([10.0, 12.0, 11.5, 15.0, 20.0, 11.0] * 5)  # 30 个数据点

    with patch.object(
        provider_with_tempo._tempo_client, "query_range", return_value=mock_values
    ):
        result = provider_with_tempo._query(
            metric='{ .service_name = "frontend" }', data_source="tempo"
        )

        assert result["data_source"] == "tempo"
        assert result["metric"] == '{ .service_name = "frontend" }'
        assert "status" in result
        assert "anomaly_count" in result
        assert "anomalies" in result


def test_query_tempo_no_data(provider_with_tempo):
    """测试 Tempo 查询无数据的情况。"""
    with patch.object(
        provider_with_tempo._tempo_client, "query_range", return_value=np.array([])
    ):
        result = provider_with_tempo._query(
            metric='{ .service_name = "frontend" }', data_source="tempo"
        )

        assert result["status"] == "no_data"
        assert result["data_source"] == "tempo"
        assert result["anomaly_count"] == 0


def test_query_tempo_not_enabled(provider_with_tempo):
    """测试 Tempo 未启用时的错误处理。"""
    # 临时禁用 Tempo
    provider_with_tempo.authentication_config.tempo_enabled = False

    with pytest.raises(ValueError, match="数据源 'tempo' 未在 Provider 配置中启用"):
        provider_with_tempo._query(
            metric='{ .service_name = "frontend" }', data_source="tempo"
        )


def test_query_tempo_traceql_passed_through(provider_with_tempo):
    """测试 TraceQL 查询语句直接传递给 TempoClient。"""
    traceql_query = '{ .service_name = "frontend" } | count() by (status_code)'
    mock_values = np.array([10.0] * 30)

    with patch.object(
        provider_with_tempo._tempo_client, "query_range", return_value=mock_values
    ) as mock_query:
        provider_with_tempo._query(metric=traceql_query, data_source="tempo")

        # 验证 TraceQL 查询被正确传递
        call_args = mock_query.call_args
        assert call_args[1]["query"] == traceql_query


def test_query_tempo_insufficient_data(provider_with_tempo):
    """测试 Tempo 数据点不足的情况。"""
    # 返回少于 min_data_points 的数据点
    mock_values = np.array([10.0, 12.0, 11.5])

    with patch.object(
        provider_with_tempo._tempo_client, "query_range", return_value=mock_values
    ):
        result = provider_with_tempo._query(
            metric='{ .service_name = "frontend" }', data_source="tempo"
        )

        assert result["status"] == "insufficient_data"
        assert result["data_points"] < result["min_data_points"]

