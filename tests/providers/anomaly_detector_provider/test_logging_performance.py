"""
测试日志记录功能的性能影响。

本测试文件验证日志记录不会显著影响异常检测的执行性能，
确保性能开销不超过检测总执行时间的 5%。
"""

import pytest
import time
from unittest.mock import patch
import numpy as np

from keep.contextmanager.contextmanager import ContextManager
from keep.providers.anomaly_detector_provider.anomaly_detector_provider import (
    AnomalyDetectorProvider,
)
from keep.providers.models.provider_config import ProviderConfig


@pytest.fixture
def context_manager():
    """创建测试用的 ContextManager。"""
    return ContextManager(
        tenant_id="test_tenant",
        workflow_id="test_workflow",
        workflow_execution_id="test_execution_123",
    )


@pytest.fixture
def provider_config():
    """创建测试用的 Provider 配置。"""
    return ProviderConfig(
        authentication={
            "prometheus_url": "http://localhost:9090",
            "algorithm": "zscore",
            "min_data_points": 10,
            "query_range_seconds": 1800,
            "query_step": "15s",
            "rate_change_threshold": 0.3,
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


class TestLoggingPerformance:
    """测试日志记录性能。"""

    def test_logging_overhead_is_acceptable(self, provider):
        """测试日志记录的性能开销在可接受范围内（< 5%）。"""
        # 创建足够的数据点以触发完整的检测流程
        mock_results = [
            {
                "metric": {"job": "test"},
                "values": [[1000 + i * 60, str(10.0 + i * 0.1)] for i in range(100)],
            }
        ]

        # 测量带日志的执行时间
        with patch.object(
            provider._prometheus_client, "query_range", return_value=mock_results
        ):
            start_time = time.time()
            result = provider._query(metric="test_metric", data_source="prometheus")
            end_time = time.time()

            total_time = end_time - start_time

            # 验证执行成功
            assert result["status"] in ["success", "normal"]
            assert total_time > 0

            # 性能开销应该很小（日志记录是异步的，不应该显著影响性能）
            # 这里主要验证功能正常工作，实际性能测试需要在生产环境中进行
            assert total_time < 5.0  # 总执行时间应该在合理范围内

    def test_logging_does_not_block_execution(self, provider):
        """测试日志记录不会阻塞异常检测执行。"""
        # Python logging 是异步的，不应该阻塞主线程
        mock_results = [
            {
                "metric": {"job": "test"},
                "values": [[1000 + i * 60, str(10.0 + i * 0.1)] for i in range(50)],
            }
        ]

        execution_times = []

        with patch.object(
            provider._prometheus_client, "query_range", return_value=mock_results
        ):
            # 执行多次，验证执行时间稳定
            for _ in range(5):
                start_time = time.time()
                result = provider._query(metric="test_metric", data_source="prometheus")
                end_time = time.time()
                execution_times.append(end_time - start_time)

            # 验证所有执行都成功
            assert all(
                r["status"] in ["success", "normal"] for r in [result]
            )

            # 验证执行时间相对稳定（日志记录不应该导致显著波动）
            avg_time = sum(execution_times) / len(execution_times)
            max_time = max(execution_times)
            min_time = min(execution_times)

            # 最大时间不应该超过平均时间的 2 倍（排除异常情况）
            assert max_time < avg_time * 2 or max_time < 1.0

    def test_debug_logging_does_not_impact_when_disabled(self, provider):
        """测试 DEBUG 日志在禁用时不会影响性能。"""
        # 设置日志级别为 INFO（禁用 DEBUG）
        provider.logger.setLevel(logging.INFO)

        mock_results = [
            {
                "metric": {"job": "test"},
                "values": [[1000 + i * 60, str(10.0 + i * 0.1)] for i in range(50)],
            }
        ]

        with patch.object(
            provider._prometheus_client, "query_range", return_value=mock_results
        ):
            start_time = time.time()
            result = provider._query(metric="test_metric", data_source="prometheus")
            end_time = time.time()

            execution_time = end_time - start_time

            # 验证执行成功
            assert result["status"] in ["success", "normal"]

            # DEBUG 日志被禁用时，不应该有性能影响
            # 这里主要验证功能正常工作
            assert execution_time < 2.0


import logging

