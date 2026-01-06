"""
测试日志集成功能。

本测试文件验证异常检测 Provider 产生的日志能够被日志聚合系统（如 Loki）正确收集和查询，
且包含适当的标签和元数据以便过滤和搜索。
"""

import pytest
import threading
from unittest.mock import patch, MagicMock
import json
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


class TestLogAggregationLabels:
    """测试日志聚合标签。"""

    def test_all_logs_include_provider_type(self, provider):
        """测试所有日志都包含 provider_type 标签。"""
        mock_results = [
            {
                "metric": {"job": "test"},
                "values": [[1000 + i * 60, str(10.0 + i * 0.1)] for i in range(20)],
            }
        ]

        captured_logs = []

        def capture_all_logs(level, msg, *args, **kwargs):
            if "extra" in kwargs:
                captured_logs.append(kwargs["extra"])

        with patch.object(
            provider._prometheus_client, "query_range", return_value=mock_results
        ), patch.object(provider.logger, "info", side_effect=capture_all_logs), patch.object(
            provider.logger, "warning", side_effect=capture_all_logs
        ), patch.object(
            provider.logger, "debug", side_effect=capture_all_logs
        ), patch.object(
            provider.logger, "error", side_effect=capture_all_logs
        ):
            provider._query(metric="test_metric", data_source="prometheus")

            # 验证所有日志都包含 provider_type
            assert len(captured_logs) > 0
            for log in captured_logs:
                assert "provider_type" in log, f"日志缺少 provider_type 字段: {log}"
                assert log["provider_type"] == "anomaly_detector"

    def test_logs_include_workflow_context_labels(self, provider):
        """测试日志包含工作流上下文标签。"""
        mock_results = [
            {
                "metric": {"job": "test"},
                "values": [[1000 + i * 60, str(10.0 + i * 0.1)] for i in range(20)],
            }
        ]

        captured_logs = []

        def capture_all_logs(level, msg, *args, **kwargs):
            if "extra" in kwargs:
                captured_logs.append(kwargs["extra"])

        with patch.object(
            provider._prometheus_client, "query_range", return_value=mock_results
        ), patch.object(provider.logger, "info", side_effect=capture_all_logs):
            provider._query(metric="test_metric", data_source="prometheus")

            # 验证所有日志都包含工作流上下文
            assert len(captured_logs) > 0
            for log in captured_logs:
                assert "workflow_id" in log
                assert log["workflow_id"] == "test_workflow"
                assert "workflow_execution_id" in log
                assert log["workflow_execution_id"] == "test_execution_123"
                assert "tenant_id" in log
                assert log["tenant_id"] == "test_tenant"

    def test_logs_include_step_id_when_available(self, provider):
        """测试日志包含 step_id（如果可用）。"""
        mock_results = [
            {
                "metric": {"job": "test"},
                "values": [[1000 + i * 60, str(10.0 + i * 0.1)] for i in range(20)],
            }
        ]

        # 设置线程上下文中的 step_id
        thread = threading.current_thread()
        thread.step_id = "test_step_123"

        captured_logs = []

        def capture_all_logs(level, msg, *args, **kwargs):
            if "extra" in kwargs:
                captured_logs.append(kwargs["extra"])

        try:
            with patch.object(
                provider._prometheus_client, "query_range", return_value=mock_results
            ), patch.object(provider.logger, "info", side_effect=capture_all_logs):
                provider._query(metric="test_metric", data_source="prometheus")

                # 验证所有日志都包含 step_id
                assert len(captured_logs) > 0
                for log in captured_logs:
                    assert "step_id" in log
                    assert log["step_id"] == "test_step_123"
        finally:
            # 清理
            thread.step_id = None


class TestLogFormatCompatibility:
    """测试日志格式兼容性。"""

    def test_logs_are_json_serializable(self, provider):
        """测试日志可以被序列化为 JSON（Loki 要求）。"""
        mock_results = [
            {
                "metric": {"job": "test"},
                "values": [[1000 + i * 60, str(10.0 + i * 0.1)] for i in range(20)],
            }
        ]

        captured_logs = []

        def capture_all_logs(level, msg, *args, **kwargs):
            if "extra" in kwargs:
                log_entry = {
                    "level": level,
                    "message": msg,
                    **kwargs["extra"],
                }
                # 尝试序列化为 JSON
                try:
                    json_str = json.dumps(log_entry, ensure_ascii=False, default=str)
                    captured_logs.append(json.loads(json_str))
                except (TypeError, ValueError) as e:
                    pytest.fail(f"日志无法序列化为 JSON: {e}")

        with patch.object(
            provider._prometheus_client, "query_range", return_value=mock_results
        ), patch.object(provider.logger, "info", side_effect=capture_all_logs):
            provider._query(metric="test_metric", data_source="prometheus")

            # 验证所有日志都可以序列化
            assert len(captured_logs) > 0
            for log in captured_logs:
                # 验证可以再次序列化
                json.dumps(log, ensure_ascii=False, default=str)

    def test_logs_follow_keep_logging_standard(self, provider):
        """测试日志遵循 Keep 的日志标准。"""
        # Keep 的日志标准要求：
        # 1. 使用 extra 参数传递结构化字段
        # 2. 字段值应该是 JSON 可序列化的类型
        # 3. 包含时间戳、日志级别、消息内容

        mock_results = [
            {
                "metric": {"job": "test"},
                "values": [[1000 + i * 60, str(10.0 + i * 0.1)] for i in range(20)],
            }
        ]

        captured_logs = []

        def capture_all_logs(level, msg, *args, **kwargs):
            if "extra" in kwargs:
                log_entry = {
                    "level": logging.getLevelName(level),
                    "message": msg,
                    **kwargs["extra"],
                }
                captured_logs.append(log_entry)

        with patch.object(
            provider._prometheus_client, "query_range", return_value=mock_results
        ), patch.object(provider.logger, "info", side_effect=capture_all_logs):
            provider._query(metric="test_metric", data_source="prometheus")

            # 验证日志格式
            assert len(captured_logs) > 0
            for log in captured_logs:
                # 验证必需字段
                assert "level" in log
                assert "message" in log
                assert "provider_type" in log

                # 验证字段值类型（应该是 JSON 可序列化的）
                for key, value in log.items():
                    assert isinstance(
                        value,
                        (str, int, float, bool, type(None), list, dict),
                    ), f"字段 {key} 的值类型 {type(value)} 不是 JSON 可序列化的"


class TestLogQueryCompatibility:
    """测试日志查询兼容性。"""

    def test_logs_can_be_filtered_by_provider_type(self, provider):
        """测试可以通过 provider_type 过滤日志。"""
        # 模拟 Loki 查询: {provider_type="anomaly_detector"}
        mock_results = [
            {
                "metric": {"job": "test"},
                "values": [[1000 + i * 60, str(10.0 + i * 0.1)] for i in range(20)],
            }
        ]

        captured_logs = []

        def capture_all_logs(level, msg, *args, **kwargs):
            if "extra" in kwargs:
                log_entry = {
                    "message": msg,
                    **kwargs["extra"],
                }
                captured_logs.append(log_entry)

        with patch.object(
            provider._prometheus_client, "query_range", return_value=mock_results
        ), patch.object(provider.logger, "info", side_effect=capture_all_logs):
            provider._query(metric="test_metric", data_source="prometheus")

            # 模拟 LogQL 过滤
            filtered_logs = [
                log
                for log in captured_logs
                if log.get("provider_type") == "anomaly_detector"
            ]

            assert len(filtered_logs) == len(captured_logs)

    def test_logs_can_be_filtered_by_workflow_id(self, provider):
        """测试可以通过 workflow_id 过滤日志。"""
        # 模拟 Loki 查询: {provider_type="anomaly_detector", workflow_id="test_workflow"}
        mock_results = [
            {
                "metric": {"job": "test"},
                "values": [[1000 + i * 60, str(10.0 + i * 0.1)] for i in range(20)],
            }
        ]

        captured_logs = []

        def capture_all_logs(level, msg, *args, **kwargs):
            if "extra" in kwargs:
                log_entry = {
                    "message": msg,
                    **kwargs["extra"],
                }
                captured_logs.append(log_entry)

        with patch.object(
            provider._prometheus_client, "query_range", return_value=mock_results
        ), patch.object(provider.logger, "info", side_effect=capture_all_logs):
            provider._query(metric="test_metric", data_source="prometheus")

            # 模拟 LogQL 过滤
            filtered_logs = [
                log
                for log in captured_logs
                if log.get("workflow_id") == "test_workflow"
            ]

            assert len(filtered_logs) == len(captured_logs)

    def test_logs_can_be_filtered_by_tenant_id(self, provider):
        """测试可以通过 tenant_id 过滤日志。"""
        # 模拟 Loki 查询: {provider_type="anomaly_detector", tenant_id="test_tenant"}
        mock_results = [
            {
                "metric": {"job": "test"},
                "values": [[1000 + i * 60, str(10.0 + i * 0.1)] for i in range(20)],
            }
        ]

        captured_logs = []

        def capture_all_logs(level, msg, *args, **kwargs):
            if "extra" in kwargs:
                log_entry = {
                    "message": msg,
                    **kwargs["extra"],
                }
                captured_logs.append(log_entry)

        with patch.object(
            provider._prometheus_client, "query_range", return_value=mock_results
        ), patch.object(provider.logger, "info", side_effect=capture_all_logs):
            provider._query(metric="test_metric", data_source="prometheus")

            # 模拟 LogQL 过滤
            filtered_logs = [
                log
                for log in captured_logs
                if log.get("tenant_id") == "test_tenant"
            ]

            assert len(filtered_logs) == len(captured_logs)

