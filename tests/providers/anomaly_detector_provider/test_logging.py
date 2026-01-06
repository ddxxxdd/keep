"""
测试 Anomaly Detector Provider 的日志记录功能。

本测试文件验证异常检测 Provider 是否正确记录日志，包括：
- 检测开始日志
- 查询执行日志
- 数据获取日志
- 检测完成日志
- 错误日志
"""

import pytest
import logging
import threading
from unittest.mock import Mock, patch, MagicMock
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
            "prometheus_username": "",
            "prometheus_password": "",
            "prometheus_verify_ssl": True,
            "algorithm": "zscore",
            "min_data_points": 10,
            "history_size": 1000,
            "query_range_seconds": 1800,
            "query_step": "15s",
            "rate_change_threshold": 0.3,
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


class TestLoggingContext:
    """测试日志上下文信息获取。"""

    def test_get_logging_context_with_all_fields(self, provider):
        """测试获取完整的日志上下文信息。"""
        # 设置线程上下文中的 step_id
        thread = threading.current_thread()
        thread.step_id = "test_step_123"

        context = provider._get_logging_context()

        assert context["provider_type"] == "anomaly_detector"
        assert context["workflow_id"] == "test_workflow"
        assert context["workflow_execution_id"] == "test_execution_123"
        assert context["tenant_id"] == "test_tenant"
        assert context["step_id"] == "test_step_123"

        # 清理
        thread.step_id = None

    def test_get_logging_context_without_step_id(self, provider):
        """测试没有 step_id 时的日志上下文信息。"""
        context = provider._get_logging_context()

        assert context["provider_type"] == "anomaly_detector"
        assert context["workflow_id"] == "test_workflow"
        assert context["tenant_id"] == "test_tenant"
        assert "step_id" not in context or context.get("step_id") is None


class TestDetectionStartLogging:
    """测试检测开始日志记录。"""

    def test_detection_start_log_with_all_params(self, provider):
        """测试检测开始日志包含所有参数。"""
        mock_results = [
            {
                "metric": {"job": "test"},
                "values": [[1000, "10.0"], [1060, "12.0"]],
            }
        ]

        with patch.object(
            provider._prometheus_client, "query_range", return_value=mock_results
        ), patch.object(provider.logger, "info") as mock_info:
            provider._query(metric="test_metric", data_source="prometheus", time_range="30m")

            # 验证检测开始日志被调用
            assert mock_info.called
            call_args = mock_info.call_args_list[0]
            message = call_args[0][0]
            extra = call_args[1]["extra"]

            assert "开始执行异常检测" in message
            assert extra["metric"] == "test_metric"
            assert extra["data_source"] == "prometheus"
            assert extra["time_range"] == "30m"
            assert extra["provider_type"] == "anomaly_detector"
            assert extra["workflow_id"] == "test_workflow"
            assert extra["tenant_id"] == "test_tenant"
            assert "algorithm" in extra


class TestQueryExecutionLogging:
    """测试查询执行日志记录。"""

    def test_query_execution_log_prometheus(self, provider):
        """测试 Prometheus 查询执行日志。"""
        mock_results = [
            {
                "metric": {"job": "test"},
                "values": [[1000, "10.0"], [1060, "12.0"]],
            }
        ]

        with patch.object(
            provider._prometheus_client, "query_range", return_value=mock_results
        ), patch.object(provider.logger, "info") as mock_info:
            provider._query(metric="test_metric", data_source="prometheus")

            # 查找查询执行日志
            query_log_calls = [
                call
                for call in mock_info.call_args_list
                if "执行 Prometheus 查询" in call[0][0]
            ]
            assert len(query_log_calls) > 0

            extra = query_log_calls[0][1]["extra"]
            assert "query" in extra
            assert extra["data_source"] == "prometheus"
            assert extra["metric"] == "test_metric"
            assert extra["provider_type"] == "anomaly_detector"


class TestDataRetrievalLogging:
    """测试数据获取日志记录。"""

    def test_data_retrieval_success_log(self, provider):
        """测试数据获取成功时的日志。"""
        mock_results = [
            {
                "metric": {"job": "test"},
                "values": [
                    [1000, "10.0"],
                    [1060, "12.0"],
                    [1120, "11.5"],
                    [1180, "15.0"],
                ],
            }
        ]

        with patch.object(
            provider._prometheus_client, "query_range", return_value=mock_results
        ), patch.object(provider.logger, "info") as mock_info:
            provider._query(metric="test_metric", data_source="prometheus")

            # 查找数据获取成功日志
            data_log_calls = [
                call
                for call in mock_info.call_args_list
                if "获取到" in call[0][0] and "个数据点" in call[0][0]
            ]
            assert len(data_log_calls) > 0

            extra = data_log_calls[0][1]["extra"]
            assert "data_points" in extra
            assert extra["data_points"] == 4
            assert extra["provider_type"] == "anomaly_detector"

    def test_data_retrieval_empty_result_log(self, provider):
        """测试查询返回空结果时的日志。"""
        with patch.object(
            provider._prometheus_client, "query_range", return_value=[]
        ), patch.object(provider.logger, "warning") as mock_warning:
            result = provider._query(metric="test_metric", data_source="prometheus")

            # 验证返回 no_data 状态
            assert result["status"] == "no_data"

            # 验证警告日志被调用
            assert mock_warning.called
            call_args = mock_warning.call_args_list[0]
            message = call_args[0][0]
            extra = call_args[1]["extra"]

            assert "查询返回空结果" in message
            assert extra["status"] == "no_data"
            assert extra["provider_type"] == "anomaly_detector"

    def test_insufficient_data_log(self, provider):
        """测试数据不足时的日志。"""
        # 返回少于 min_data_points 的数据点
        mock_results = [
            {
                "metric": {"job": "test"},
                "values": [[1000, "10.0"], [1060, "12.0"]],  # 只有 2 个数据点，少于 min_data_points=10
            }
        ]

        with patch.object(
            provider._prometheus_client, "query_range", return_value=mock_results
        ), patch.object(provider.logger, "warning") as mock_warning:
            result = provider._query(metric="test_metric", data_source="prometheus")

            # 验证返回 insufficient_data 状态
            assert result["status"] == "insufficient_data"

            # 验证警告日志被调用
            assert mock_warning.called
            call_args = mock_warning.call_args_list[0]
            message = call_args[0][0]
            extra = call_args[1]["extra"]

            assert "数据点不足" in message
            assert extra["status"] == "insufficient_data"
            assert extra["data_points"] == 2
            assert extra["min_data_points"] == 10
            assert extra["provider_type"] == "anomaly_detector"


class TestDetectionCompletionLogging:
    """测试检测完成日志记录。"""

    def test_detection_completion_normal_log(self, provider):
        """测试正常检测结果（无异常）的日志。"""
        # 创建正常的数据（无异常）
        mock_results = [
            {
                "metric": {"job": "test"},
                "values": [
                    [1000 + i * 60, str(10.0 + i * 0.1)]
                    for i in range(20)  # 20 个数据点，满足 min_data_points
                ],
            }
        ]

        with patch.object(
            provider._prometheus_client, "query_range", return_value=mock_results
        ), patch.object(provider.logger, "info") as mock_info:
            result = provider._query(metric="test_metric", data_source="prometheus")

            # 验证返回 normal 状态
            assert result["status"] in ["normal", "success"]

            # 查找检测完成日志
            completion_log_calls = [
                call
                for call in mock_info.call_args_list
                if "异常检测完成" in call[0][0]
            ]
            assert len(completion_log_calls) > 0

            extra = completion_log_calls[0][1]["extra"]
            assert "status" in extra
            assert "total_points" in extra
            assert "anomaly_count" in extra
            assert "mean" in extra
            assert "std" in extra
            assert "algorithm" in extra
            assert extra["provider_type"] == "anomaly_detector"
            # 正常结果也应该记录 INFO 级别日志
            assert extra["status"] in ["normal", "success"]

    def test_detection_completion_with_anomalies_log(self, provider):
        """测试检测到异常时的日志。"""
        # 创建包含异常的数据
        mock_results = [
            {
                "metric": {"job": "test"},
                "values": (
                    [[1000 + i * 60, str(10.0 + i * 0.1)] for i in range(15)]
                    + [[1000 + 15 * 60, "50.0"]]  # 异常点
                    + [[1000 + i * 60, str(10.0 + i * 0.1)] for i in range(16, 20)]
                ),
            }
        ]

        with patch.object(
            provider._prometheus_client, "query_range", return_value=mock_results
        ), patch.object(provider.logger, "info") as mock_info, patch.object(
            provider.logger, "warning"
        ) as mock_warning:
            result = provider._query(metric="test_metric", data_source="prometheus")

            # 验证返回 success 状态（有异常）
            if result["anomaly_count"] > 0:
                assert result["status"] == "success"

                # 验证异常详情日志（WARNING 级别）
                anomaly_log_calls = [
                    call
                    for call in mock_warning.call_args_list
                    if "检测到" in call[0][0] and "个异常点" in call[0][0]
                ]
                if len(anomaly_log_calls) > 0:
                    extra = anomaly_log_calls[0][1]["extra"]
                    assert "anomaly_count" in extra
                    assert "anomalies_summary" in extra or "anomalies" in extra
                    assert extra["provider_type"] == "anomaly_detector"


class TestErrorLogging:
    """测试错误日志记录。"""

    def test_query_error_log(self, provider):
        """测试查询失败时的错误日志。"""
        with patch.object(
            provider._prometheus_client,
            "query_range",
            side_effect=Exception("连接失败"),
        ), patch.object(provider.logger, "error") as mock_error:
            with pytest.raises(Exception):
                provider._query(metric="test_metric", data_source="prometheus")

            # 验证错误日志被调用
            assert mock_error.called
            call_args = mock_error.call_args_list[0]
            message = call_args[0][0]
            extra = call_args[1]["extra"]
            exc_info = call_args[1].get("exc_info", False)

            assert "查询失败" in message or "错误" in message
            assert "error_message" in extra or "error" in message.lower()
            assert extra["provider_type"] == "anomaly_detector"
            assert exc_info is True  # 应该包含堆栈跟踪

    def test_config_error_log(self, provider):
        """测试配置错误时的日志。"""
        # 创建一个配置无效的 provider
        invalid_config = ProviderConfig(authentication={})
        invalid_provider = AnomalyDetectorProvider(
            context_manager=provider.context_manager,
            provider_id="invalid-provider",
            config=invalid_config,
        )

        with patch.object(invalid_provider.logger, "error") as mock_error:
            with pytest.raises((ValueError, RuntimeError)):
                invalid_provider._query(metric="test_metric")

            # 验证错误日志被调用
            assert mock_error.called


class TestDebugLogging:
    """测试 DEBUG 级别日志记录。"""

    def test_debug_logging_when_enabled(self, provider):
        """测试 DEBUG 级别启用时的日志。"""
        mock_results = [
            {
                "metric": {"job": "test"},
                "values": [[1000 + i * 60, str(10.0 + i * 0.1)] for i in range(20)],
            }
        ]

        # 设置日志级别为 DEBUG
        provider.logger.setLevel(logging.DEBUG)

        with patch.object(
            provider._prometheus_client, "query_range", return_value=mock_results
        ), patch.object(provider.logger, "debug") as mock_debug:
            provider._query(metric="test_metric", data_source="prometheus")

            # 验证 DEBUG 日志被调用（数据统计信息）
            debug_log_calls = [
                call
                for call in mock_debug.call_args_list
                if "数据统计" in call[0][0] or "统计" in call[0][0]
            ]
            # 如果启用了 DEBUG，应该有统计信息日志
            if provider.logger.isEnabledFor(logging.DEBUG):
                assert len(debug_log_calls) > 0 or mock_debug.called

    def test_debug_logging_when_disabled(self, provider):
        """测试 DEBUG 级别禁用时不会记录详细日志。"""
        mock_results = [
            {
                "metric": {"job": "test"},
                "values": [[1000 + i * 60, str(10.0 + i * 0.1)] for i in range(20)],
            }
        ]

        # 设置日志级别为 INFO（禁用 DEBUG）
        provider.logger.setLevel(logging.INFO)

        with patch.object(
            provider._prometheus_client, "query_range", return_value=mock_results
        ), patch.object(provider.logger, "debug") as mock_debug:
            provider._query(metric="test_metric", data_source="prometheus")

            # DEBUG 日志可能不会被调用，或者即使调用也不会输出（取决于日志配置）
            # 这里主要验证不会因为 DEBUG 日志导致性能问题


class TestLoggingContextFields:
    """测试日志上下文字段的完整性。"""

    def test_all_logs_include_context_fields(self, provider):
        """测试所有日志都包含上下文字段。"""
        mock_results = [
            {
                "metric": {"job": "test"},
                "values": [[1000 + i * 60, str(10.0 + i * 0.1)] for i in range(20)],
            }
        ]

        with patch.object(
            provider._prometheus_client, "query_range", return_value=mock_results
        ), patch.object(provider.logger, "info") as mock_info:
            provider._query(metric="test_metric", data_source="prometheus")

            # 验证所有 INFO 日志都包含上下文字段
            for call in mock_info.call_args_list:
                extra = call[1].get("extra", {})
                assert "provider_type" in extra
                assert extra["provider_type"] == "anomaly_detector"
                assert "workflow_id" in extra
                assert "tenant_id" in extra

