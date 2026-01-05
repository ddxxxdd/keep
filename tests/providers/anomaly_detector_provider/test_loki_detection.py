"""
测试 Anomaly Detector Provider 的 Loki logs 异常检测功能。
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
def provider_config_with_loki():
    """创建启用 Loki 的 Provider 配置。"""
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
            "tempo_enabled": False,
            "loki_url": "http://localhost:3100",
            "loki_enabled": True,  # 启用 Loki
        }
    )


@pytest.fixture
def provider_with_loki(context_manager, provider_config_with_loki):
    """创建启用 Loki 的 Provider 实例。"""
    return AnomalyDetectorProvider(
        context_manager=context_manager,
        provider_id="test-anomaly-detector-loki",
        config=provider_config_with_loki,
    )


def test_query_loki_success(provider_with_loki):
    """测试 Loki 查询成功的情况。"""
    # Mock LokiClient 返回的时间序列数据（numpy array）
    mock_values = np.array([45.0, 50.0, 48.0, 55.0, 70.0, 47.0] * 5)  # 30 个数据点

    with patch.object(
        provider_with_loki._loki_client, "query_range", return_value=mock_values
    ):
        result = provider_with_loki._query(
            metric='sum(rate({job="varlogs"}[5m])) by (level)', data_source="loki"
        )

        assert result["data_source"] == "loki"
        assert result["metric"] == 'sum(rate({job="varlogs"}[5m])) by (level)'
        assert "status" in result
        assert "anomaly_count" in result
        assert "anomalies" in result


def test_query_loki_no_data(provider_with_loki):
    """测试 Loki 查询无数据的情况。"""
    with patch.object(
        provider_with_loki._loki_client, "query_range", return_value=np.array([])
    ):
        result = provider_with_loki._query(
            metric='sum(rate({job="varlogs"}[5m]))', data_source="loki"
        )

        assert result["status"] == "no_data"
        assert result["data_source"] == "loki"
        assert result["anomaly_count"] == 0


def test_query_loki_not_enabled(provider_with_loki):
    """测试 Loki 未启用时的错误处理。"""
    # 临时禁用 Loki
    provider_with_loki.authentication_config.loki_enabled = False

    with pytest.raises(ValueError, match="数据源 'loki' 未在 Provider 配置中启用"):
        provider_with_loki._query(
            metric='sum(rate({job="varlogs"}[5m]))', data_source="loki"
        )


def test_query_loki_logql_passed_through(provider_with_loki):
    """测试 LogQL 查询语句直接传递给 LokiClient。"""
    logql_query = 'sum(rate({job="varlogs", level="error"}[5m]))'
    mock_values = np.array([10.0] * 30)

    with patch.object(
        provider_with_loki._loki_client, "query_range", return_value=mock_values
    ) as mock_query:
        provider_with_loki._query(metric=logql_query, data_source="loki")

        # 验证 LogQL 查询被正确传递
        call_args = mock_query.call_args
        assert call_args[1]["query"] == logql_query


def test_query_loki_insufficient_data(provider_with_loki):
    """测试 Loki 数据点不足的情况。"""
    # 返回少于 min_data_points 的数据点
    mock_values = np.array([45.0, 50.0, 48.0])

    with patch.object(
        provider_with_loki._loki_client, "query_range", return_value=mock_values
    ):
        result = provider_with_loki._query(
            metric='sum(rate({job="varlogs"}[5m]))', data_source="loki"
        )

        assert result["status"] == "insufficient_data"
        assert result["data_points"] < result["min_data_points"]

