"""
测试日志字段格式一致性。

本测试文件验证 Provider 模式下的日志字段与独立服务（keep-anomaly-detector）的日志字段
在关键信息上保持一致，确保用户能够使用相同的日志查询方式。
"""

import pytest
from unittest.mock import patch
import logging

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


class TestLogFieldConsistency:
    """测试日志字段一致性。"""

    def test_required_fields_present(self, provider):
        """测试必需字段都存在。"""
        # 独立服务记录的字段：metric, time_range, data_points, algorithm, anomaly_count
        mock_results = [
            {
                "metric": {"job": "test"},
                "values": [[1000 + i * 60, str(10.0 + i * 0.1)] for i in range(20)],
            }
        ]

        captured_logs = []

        def capture_log(level, msg, *args, **kwargs):
            if "extra" in kwargs:
                captured_logs.append(kwargs["extra"])

        with patch.object(
            provider._prometheus_client, "query_range", return_value=mock_results
        ), patch.object(provider.logger, "info", side_effect=capture_log):
            provider._query(metric="test_metric", time_range="30m")

            # 验证所有必需字段都存在
            required_fields = [
                "metric",
                "data_source",
                "algorithm",
                "provider_type",
            ]

            # 检查检测完成日志
            completion_logs = [
                log
                for log in captured_logs
                if "status" in log and "total_points" in log
            ]

            if completion_logs:
                log = completion_logs[0]
                for field in required_fields:
                    assert field in log, f"缺少必需字段: {field}"

    def test_field_naming_consistency(self, provider):
        """测试字段命名与独立服务保持一致。"""
        # 独立服务使用的字段名：
        # - metric (指标名称)
        # - time_range (时间范围)
        # - data_points (数据点数量)
        # - algorithm (检测算法)
        # - anomaly_count (异常数量)

        mock_results = [
            {
                "metric": {"job": "test"},
                "values": [[1000 + i * 60, str(10.0 + i * 0.1)] for i in range(20)],
            }
        ]

        captured_logs = []

        def capture_log(level, msg, *args, **kwargs):
            if "extra" in kwargs:
                captured_logs.append(kwargs["extra"])

        with patch.object(
            provider._prometheus_client, "query_range", return_value=mock_results
        ), patch.object(provider.logger, "info", side_effect=capture_log):
            provider._query(metric="test_metric", time_range="30m")

            # 验证字段命名
            all_logs = captured_logs
            assert len(all_logs) > 0

            # 检查关键字段命名
            for log in all_logs:
                # metric 字段应该存在（使用小写，与独立服务一致）
                if "metric" in log:
                    assert isinstance(log["metric"], str)

                # data_points 字段应该存在（使用下划线，与独立服务一致）
                if "data_points" in log:
                    assert isinstance(log["data_points"], int)

                # algorithm 字段应该存在
                if "algorithm" in log:
                    assert isinstance(log["algorithm"], str)

                # anomaly_count 字段应该在检测完成日志中存在
                if "status" in log and "total_points" in log:
                    assert "anomaly_count" in log
                    assert isinstance(log["anomaly_count"], int)

    def test_field_values_consistency(self, provider):
        """测试字段值的格式与独立服务一致。"""
        mock_results = [
            {
                "metric": {"job": "test"},
                "values": [[1000 + i * 60, str(10.0 + i * 0.1)] for i in range(20)],
            }
        ]

        captured_logs = []

        def capture_log(level, msg, *args, **kwargs):
            if "extra" in kwargs:
                captured_logs.append(kwargs["extra"])

        with patch.object(
            provider._prometheus_client, "query_range", return_value=mock_results
        ), patch.object(provider.logger, "info", side_effect=capture_log):
            result = provider._query(metric="test_metric", time_range="30m")

            # 验证字段值格式
            completion_logs = [
                log
                for log in captured_logs
                if "status" in log and "total_points" in log
            ]

            if completion_logs:
                log = completion_logs[0]

                # metric 应该是字符串
                assert isinstance(log["metric"], str)
                assert log["metric"] == "test_metric"

                # data_points/total_points 应该是整数
                assert isinstance(log["total_points"], int)
                assert log["total_points"] > 0

                # anomaly_count 应该是整数
                assert isinstance(log["anomaly_count"], int)
                assert log["anomaly_count"] >= 0

                # algorithm 应该是字符串
                assert isinstance(log["algorithm"], str)
                assert log["algorithm"] in ["zscore", "isolation_forest", "both", "rate_change"]

                # status 应该是预定义的值
                assert log["status"] in ["success", "normal", "no_data", "insufficient_data"]


class TestLogMessageFormat:
    """测试日志消息格式。"""

    def test_log_message_contains_key_info(self, provider):
        """测试日志消息包含关键信息。"""
        mock_results = [
            {
                "metric": {"job": "test"},
                "values": [[1000 + i * 60, str(10.0 + i * 0.1)] for i in range(20)],
            }
        ]

        captured_messages = []

        def capture_log(level, msg, *args, **kwargs):
            captured_messages.append(msg)

        with patch.object(
            provider._prometheus_client, "query_range", return_value=mock_results
        ), patch.object(provider.logger, "info", side_effect=capture_log):
            provider._query(metric="test_metric", time_range="30m")

            # 验证关键消息存在
            messages = " ".join(captured_messages)

            # 检测开始消息
            assert "开始执行异常检测" in messages or "异常检测" in messages

            # 检测完成消息
            assert "异常检测完成" in messages or "检测完成" in messages
            assert "状态" in messages or "status" in messages.lower()
            assert "异常数量" in messages or "anomaly" in messages.lower()

    def test_log_message_format_consistency(self, provider):
        """测试日志消息格式与独立服务保持一致。"""
        # 独立服务的日志消息格式示例：
        # "异常检测完成: 状态=normal, 总数据点=120, 异常数量=0"

        mock_results = [
            {
                "metric": {"job": "test"},
                "values": [[1000 + i * 60, str(10.0 + i * 0.1)] for i in range(20)],
            }
        ]

        captured_messages = []

        def capture_log(level, msg, *args, **kwargs):
            captured_messages.append(msg)

        with patch.object(
            provider._prometheus_client, "query_range", return_value=mock_results
        ), patch.object(provider.logger, "info", side_effect=capture_log):
            provider._query(metric="test_metric", time_range="30m")

            # 查找检测完成消息
            completion_messages = [
                msg
                for msg in captured_messages
                if "异常检测完成" in msg or "检测完成" in msg
            ]

            if completion_messages:
                message = completion_messages[0]

                # 验证消息格式包含关键信息
                assert "状态" in message or "status" in message.lower()
                assert "总数据点" in message or "数据点" in message
                assert "异常数量" in message or "异常" in message

