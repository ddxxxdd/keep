"""
测试 Anomaly Detector Provider 返回结果的结构，确保与工作流 YAML 引用方式兼容。
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


def test_result_structure_has_all_required_fields(provider):
    """测试返回结果包含所有必需字段。"""
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

        # 验证所有必需字段存在
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


def test_result_field_types_are_correct(provider):
    """测试返回结果字段类型正确。"""
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

        # 验证字段类型
        assert isinstance(result["metric"], str)
        assert isinstance(result["query"], str)
        assert isinstance(result["data_source"], str)
        assert isinstance(result["status"], str)
        assert isinstance(result["total_points"], int)
        assert isinstance(result["anomaly_count"], int)
        assert isinstance(result["mean"], (int, float))
        assert isinstance(result["std"], (int, float))
        assert isinstance(result["algorithm"], str)
        assert isinstance(result["anomalies"], list)

        # 验证 anomalies 列表中的每个元素结构
        for anomaly in result["anomalies"]:
            assert isinstance(anomaly, dict)
            assert "index" in anomaly
            assert "value" in anomaly
            assert "score" in anomaly
            assert "method" in anomaly
            assert "details" in anomaly

            assert isinstance(anomaly["index"], int)
            assert isinstance(anomaly["value"], (int, float))
            assert isinstance(anomaly["score"], (int, float))
            assert isinstance(anomaly["method"], str)
            assert isinstance(anomaly["details"], dict)


def test_result_status_values_are_valid(provider):
    """测试 status 字段的值是有效的。"""
    valid_statuses = ["success", "normal", "no_data", "insufficient_data"]

    # 测试 no_data 状态
    with patch.object(
        provider._prometheus_client, "query_range", return_value=[]
    ):
        result = provider._query(metric="test_metric")
        assert result["status"] in valid_statuses
        assert result["status"] == "no_data"

    # 测试 insufficient_data 状态
    mock_results_insufficient = [
        {
            "metric": {"job": "test"},
            "values": [[1000, "10.0"], [1060, "12.0"]],  # 只有 2 个数据点
        }
    ]
    with patch.object(
        provider._prometheus_client,
        "query_range",
        return_value=mock_results_insufficient,
    ):
        result = provider._query(metric="test_metric")
        assert result["status"] in valid_statuses
        assert result["status"] == "insufficient_data"

    # 测试 success/normal 状态
    mock_results_sufficient = [
        {
            "metric": {"job": "test"},
            "values": [
                [1000 + i * 60, str(10.0 + i * 0.5)] for i in range(30)
            ],
        }
    ]
    with patch.object(
        provider._prometheus_client,
        "query_range",
        return_value=mock_results_sufficient,
    ):
        result = provider._query(metric="test_metric")
        assert result["status"] in valid_statuses
        assert result["status"] in ["success", "normal"]


def test_result_is_yaml_serializable(provider):
    """测试返回结果可以被 YAML 序列化（工作流引擎需要）。"""
    import yaml

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

        # 尝试序列化为 YAML
        try:
            yaml_str = yaml.dump(result, allow_unicode=True)
            # 验证可以反序列化
            parsed = yaml.safe_load(yaml_str)
            assert parsed["metric"] == result["metric"]
            assert parsed["status"] == result["status"]
        except Exception as e:
            pytest.fail(f"结果无法序列化为 YAML: {e}")


def test_result_can_be_used_in_workflow_conditions(provider):
    """测试返回结果可以在工作流条件表达式中使用。"""
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

        # 模拟工作流条件表达式中的使用
        # 例如: "{{ steps.detect-anomalies.status == 'success' and steps.detect-anomalies.anomaly_count > 0 }}"
        status = result["status"]
        anomaly_count = result["anomaly_count"]

        # 验证这些值可以用于条件判断
        assert isinstance(status, str)
        assert isinstance(anomaly_count, int)
        assert anomaly_count >= 0

        # 验证条件表达式逻辑
        if status == "success":
            assert anomaly_count > 0
        elif status == "normal":
            assert anomaly_count == 0

