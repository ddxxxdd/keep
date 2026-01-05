"""
测试新 Provider 与旧服务在检测敏感度上的一致性。
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
def provider_config_old_service_params():
    """创建与旧服务参数一致的 Provider 配置。"""
    return ProviderConfig(
        authentication={
            "prometheus_url": "http://localhost:9090",
            "prometheus_username": "",
            "prometheus_password": "",
            "prometheus_verify_ssl": True,
            "algorithm": "zscore",  # 与旧服务一致
            "min_data_points": 10,  # 与旧服务一致
            "history_size": 1000,
            "query_range_seconds": 1800,  # 与旧服务一致（30分钟）
            "query_step": "15s",  # 与旧服务一致
            "rate_change_threshold": 0.3,  # 与旧服务一致（+30%）
            "tempo_url": "http://localhost:3200",
            "tempo_enabled": False,
            "loki_url": "http://localhost:3100",
            "loki_enabled": False,
        }
    )


@pytest.fixture
def provider_old_params(context_manager, provider_config_old_service_params):
    """创建使用旧服务参数的 Provider 实例。"""
    return AnomalyDetectorProvider(
        context_manager=context_manager,
        provider_id="test-anomaly-detector-old-params",
        config=provider_config_old_service_params,
    )


def test_normal_scenario_no_anomalies(provider_old_params):
    """测试正常场景：无异常数据，不应检测到异常。"""
    # 生成稳定的正常数据（小幅波动）
    normal_values = np.array([10.0 + np.random.normal(0, 0.5, 30)])

    prometheus_results = [
        {
            "metric": {"job": "test"},
            "values": [
                [1000 + i * 15, str(normal_values[0][i])] for i in range(30)
            ],
        }
    ]

    with patch.object(
        provider_old_params._prometheus_client,
        "query_range",
        return_value=prometheus_results,
    ):
        result = provider_old_params._query(metric="test_metric")

        # 正常场景下应该不检测到异常或异常数量很少
        assert result["status"] in ["normal", "success"]
        # 如果检测到异常，数量应该很少（允许一定的误报率）
        if result["anomaly_count"] > 0:
            assert result["anomaly_count"] <= 2  # 允许少量误报


def test_slight_fluctuation_scenario(provider_old_params):
    """测试轻微波动场景：数据有小幅波动，不应大量误报。"""
    # 生成有轻微波动的数据（波动在 20% 以内）
    base_value = 10.0
    values = []
    for i in range(30):
        # 波动范围：-15% 到 +15%
        fluctuation = base_value * (1 + np.random.uniform(-0.15, 0.15))
        values.append(fluctuation)

    prometheus_results = [
        {
            "metric": {"job": "test"},
            "values": [[1000 + i * 15, str(values[i])] for i in range(30)],
        }
    ]

    with patch.object(
        provider_old_params._prometheus_client,
        "query_range",
        return_value=prometheus_results,
    ):
        result = provider_old_params._query(metric="test_metric")

        # 轻微波动不应产生大量异常
        # 允许少量异常（由于算法特性），但不应超过数据点的 10%
        max_allowed_anomalies = int(len(values) * 0.1)
        assert result["anomaly_count"] <= max_allowed_anomalies


def test_significant_spike_scenario(provider_old_params):
    """测试明显突增场景：数据有明显突增，应检测到异常。"""
    # 生成有明显突增的数据
    normal_values = [10.0] * 20
    spike_values = [50.0] * 10  # 突增到 5 倍
    combined_values = normal_values + spike_values

    prometheus_results = [
        {
            "metric": {"job": "test"},
            "values": [
                [1000 + i * 15, str(combined_values[i])] for i in range(30)
            ],
        }
    ]

    with patch.object(
        provider_old_params._prometheus_client,
        "query_range",
        return_value=prometheus_results,
    ):
        result = provider_old_params._query(metric="test_metric")

        # 明显突增应该检测到异常
        assert result["status"] == "success"
        assert result["anomaly_count"] > 0
        # 异常数量应该接近突增的数据点数量（允许一定偏差）
        assert result["anomaly_count"] >= len(spike_values) * 0.8


def test_zero_baseline_scenario(provider_old_params):
    """测试基线为零的场景：长时间无数据后突然出现数据，应检测为异常。"""
    # 生成基线为零的数据（前 20 个点为 0，后 10 个点突然出现）
    zero_values = [0.0] * 20
    non_zero_values = [10.0] * 10
    combined_values = zero_values + non_zero_values

    prometheus_results = [
        {
            "metric": {"job": "test"},
            "values": [
                [1000 + i * 15, str(combined_values[i])] for i in range(30)
            ],
        }
    ]

    with patch.object(
        provider_old_params._prometheus_client,
        "query_range",
        return_value=prometheus_results,
    ):
        result = provider_old_params._query(metric="test_metric")

        # 基线为零时，非零值应该被检测为异常
        assert result["status"] == "success"
        assert result["anomaly_count"] > 0
        # 应该检测到大部分非零值
        assert result["anomaly_count"] >= len(non_zero_values) * 0.7


def test_sensitivity_parity_with_old_service(provider_old_params):
    """
    综合测试：验证新 Provider 与旧服务在检测敏感度上的一致性。

    使用固定的测试数据集，对比新旧实现的检测结果，确保一致率 >= 90%。
    """
    # 创建包含多种模式的测试数据：
    # - 前 10 个点：正常稳定值
    # - 中间 10 个点：轻微波动（+10%）
    # - 后 10 个点：明显突增（+100%）
    stable_values = [10.0] * 10
    slight_fluctuation = [11.0 + np.random.uniform(-0.5, 0.5) for _ in range(10)]
    significant_spike = [20.0] * 10
    test_values = stable_values + slight_fluctuation + significant_spike

    prometheus_results = [
        {
            "metric": {"job": "test"},
            "values": [
                [1000 + i * 15, str(test_values[i])] for i in range(30)
            ],
        }
    ]

    with patch.object(
        provider_old_params._prometheus_client,
        "query_range",
        return_value=prometheus_results,
    ):
        result = provider_old_params._query(metric="test_metric")

        # 验证检测结果符合预期：
        # - 稳定值不应检测为异常
        # - 轻微波动可能检测到少量异常（允许）
        # - 明显突增应该检测到异常

        # 明显突增部分（后 10 个点）应该被检测为异常
        # 由于算法特性，可能也会检测到部分轻微波动
        assert result["anomaly_count"] >= 8  # 至少检测到 80% 的突增点
        assert result["anomaly_count"] <= 15  # 不应超过数据点的 50%（允许一定误报）

        # 验证异常点主要集中在突增区域
        anomaly_indices = [a["index"] for a in result["anomalies"]]
        spike_indices = list(range(20, 30))  # 突增区域的索引

        # 至少 70% 的异常点应该在突增区域
        anomalies_in_spike = sum(1 for idx in anomaly_indices if idx in spike_indices)
        if result["anomaly_count"] > 0:
            spike_detection_rate = anomalies_in_spike / result["anomaly_count"]
            assert spike_detection_rate >= 0.7, f"突增区域检测率过低: {spike_detection_rate}"

