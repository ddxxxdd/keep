"""
测试 Anomaly Detector Provider 的 Prometheus 数据源异常检测功能。
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
def provider_config():
    """创建测试用的 Provider 配置。"""
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
            "loki_enabled": False,
        }
    )


@pytest.fixture
def provider(context_manager, provider_config):
    """创建测试用的 Provider 实例。"""
    return AnomalyDetectorProvider(
        context_manager=context_manager,
        provider_id="test-anomaly-detector",
        config=provider_config,
    )


def test_query_prometheus_default_data_source(provider):
    """测试默认使用 Prometheus 数据源（data_source=None）。"""
    # Mock Prometheus 客户端返回的时间序列数据
    mock_results = [
        {
            "metric": {"job": "test"},
            "values": [
                [1000, "10.0"],
                [1060, "12.0"],
                [1120, "11.5"],
                [1180, "15.0"],
                [1240, "20.0"],  # 异常点
                [1300, "11.0"],
            ],
        }
    ]

    with patch.object(
        provider._prometheus_client, "query_range", return_value=mock_results
    ):
        result = provider._query(metric="test_metric", time_range="10m")

        assert result["data_source"] == "prometheus"
        assert result["metric"] == "test_metric"
        assert "status" in result
        assert "anomaly_count" in result
        assert "anomalies" in result


def test_query_prometheus_explicit_data_source(provider):
    """测试显式指定 Prometheus 数据源（data_source="prometheus"）。"""
    mock_results = [
        {
            "metric": {"job": "test"},
            "values": [
                [1000, "10.0"],
                [1060, "12.0"],
                [1120, "11.5"],
            ],
        }
    ]

    with patch.object(
        provider._prometheus_client, "query_range", return_value=mock_results
    ):
        result = provider._query(
            metric="test_metric", data_source="prometheus", time_range="5m"
        )

        assert result["data_source"] == "prometheus"
        assert result["metric"] == "test_metric"


def test_query_prometheus_no_data(provider):
    """测试 Prometheus 查询无数据的情况。"""
    with patch.object(
        provider._prometheus_client, "query_range", return_value=[]
    ):
        result = provider._query(metric="test_metric")

        assert result["status"] == "no_data"
        assert result["anomaly_count"] == 0
        assert len(result["anomalies"]) == 0


def test_query_prometheus_insufficient_data(provider):
    """测试数据点不足的情况。"""
    # 返回少于 min_data_points 的数据点
    mock_results = [
        {
            "metric": {"job": "test"},
            "values": [
                [1000, "10.0"],
                [1060, "12.0"],
            ],
        }
    ]

    with patch.object(
        provider._prometheus_client, "query_range", return_value=mock_results
    ):
        result = provider._query(metric="test_metric")

        assert result["status"] == "insufficient_data"
        assert "data_points" in result
        assert "min_data_points" in result
        assert result["data_points"] < result["min_data_points"]


def test_query_prometheus_counter_metric_auto_rate(provider):
    """测试计数型指标自动包装 rate()。"""
    mock_results = [
        {
            "metric": {"job": "test"},
            "values": [
                [1000, "10.0"],
                [1060, "12.0"],
            ],
        }
    ]

    with patch.object(
        provider._prometheus_client, "query_range", return_value=mock_results
    ) as mock_query:
        provider._query(metric="test_metric_count")

        # 验证 query_range 被调用，且查询表达式包含 rate()
        call_args = mock_query.call_args
        assert "rate(" in call_args[1]["query"]


def test_query_prometheus_invalid_data_source(provider):
    """测试无效的数据源类型。"""
    with pytest.raises(ValueError, match="不支持的数据源类型"):
        provider._query(metric="test_metric", data_source="invalid_source")


def test_query_prometheus_empty_metric(provider):
    """测试空指标名的情况。"""
    with pytest.raises(ValueError, match="参数 'metric' 不能为空"):
        provider._query(metric="")


def test_query_prometheus_preserves_existing_behavior(provider):
    """测试 Prometheus 查询保持原有行为不变（向后兼容）。"""
    # 生成足够的数据点以触发检测
    mock_results = [
        {
            "metric": {"job": "test"},
            "values": [
                [1000 + i * 60, str(10.0 + i * 0.5)] for i in range(30)
            ],
        }
    ]

    with patch.object(
        provider._prometheus_client, "query_range", return_value=mock_results
    ):
        result = provider._query(metric="test_metric")

        # 验证返回结构包含所有必需字段
        required_fields = [
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
        for field in required_fields:
            assert field in result, f"缺少必需字段: {field}"

