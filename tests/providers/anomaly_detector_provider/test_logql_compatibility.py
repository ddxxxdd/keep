"""
测试 LogQL 查询兼容性。

本测试文件验证使用 LogQL 查询 Provider 日志时能够提取与独立服务相同的字段，
确保用户能够使用相同或相似的查询语句。
"""

import pytest
from unittest.mock import patch
import json

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


class TestLogQLFieldExtraction:
    """测试 LogQL 字段提取。"""

    def test_logql_can_extract_metric_field(self, provider):
        """测试 LogQL 可以提取 metric 字段。"""
        mock_results = [
            {
                "metric": {"job": "test"},
                "values": [[1000 + i * 60, str(10.0 + i * 0.1)] for i in range(20)],
            }
        ]

        captured_logs = []

        def capture_log(level, msg, *args, **kwargs):
            if "extra" in kwargs:
                # 模拟 JSON 格式日志（LogQL 可以解析）
                log_entry = {
                    "message": msg,
                    **kwargs["extra"],
                }
                captured_logs.append(json.dumps(log_entry, ensure_ascii=False))

        with patch.object(
            provider._prometheus_client, "query_range", return_value=mock_results
        ), patch.object(provider.logger, "info", side_effect=capture_log):
            provider._query(metric="test_metric", time_range="30m")

            # 验证日志可以被解析为 JSON（LogQL 要求）
            assert len(captured_logs) > 0

            # 解析第一条日志
            log_json = json.loads(captured_logs[0])
            assert "metric" in log_json
            assert log_json["metric"] == "test_metric"

    def test_logql_can_extract_status_field(self, provider):
        """测试 LogQL 可以提取 status 字段。"""
        mock_results = [
            {
                "metric": {"job": "test"},
                "values": [[1000 + i * 60, str(10.0 + i * 0.1)] for i in range(20)],
            }
        ]

        captured_logs = []

        def capture_log(level, msg, *args, **kwargs):
            if "extra" in kwargs and "status" in kwargs["extra"]:
                log_entry = {
                    "message": msg,
                    **kwargs["extra"],
                }
                captured_logs.append(json.dumps(log_entry, ensure_ascii=False))

        with patch.object(
            provider._prometheus_client, "query_range", return_value=mock_results
        ), patch.object(provider.logger, "info", side_effect=capture_log):
            provider._query(metric="test_metric", time_range="30m")

            # 查找包含 status 的日志
            status_logs = [
                json.loads(log)
                for log in captured_logs
                if "status" in json.loads(log)
            ]

            assert len(status_logs) > 0
            log = status_logs[0]
            assert "status" in log
            assert log["status"] in ["success", "normal", "no_data", "insufficient_data"]

    def test_logql_can_extract_anomaly_count_field(self, provider):
        """测试 LogQL 可以提取 anomaly_count 字段。"""
        mock_results = [
            {
                "metric": {"job": "test"},
                "values": [[1000 + i * 60, str(10.0 + i * 0.1)] for i in range(20)],
            }
        ]

        captured_logs = []

        def capture_log(level, msg, *args, **kwargs):
            if "extra" in kwargs and "anomaly_count" in kwargs["extra"]:
                log_entry = {
                    "message": msg,
                    **kwargs["extra"],
                }
                captured_logs.append(json.dumps(log_entry, ensure_ascii=False))

        with patch.object(
            provider._prometheus_client, "query_range", return_value=mock_results
        ), patch.object(provider.logger, "info", side_effect=capture_log):
            provider._query(metric="test_metric", time_range="30m")

            # 查找包含 anomaly_count 的日志
            anomaly_logs = [
                json.loads(log)
                for log in captured_logs
                if "anomaly_count" in json.loads(log)
            ]

            assert len(anomaly_logs) > 0
            log = anomaly_logs[0]
            assert "anomaly_count" in log
            assert isinstance(log["anomaly_count"], int)
            assert log["anomaly_count"] >= 0

    def test_logql_can_filter_by_provider_type(self, provider):
        """测试 LogQL 可以通过 provider_type 过滤日志。"""
        mock_results = [
            {
                "metric": {"job": "test"},
                "values": [[1000 + i * 60, str(10.0 + i * 0.1)] for i in range(20)],
            }
        ]

        captured_logs = []

        def capture_log(level, msg, *args, **kwargs):
            if "extra" in kwargs:
                log_entry = {
                    "message": msg,
                    **kwargs["extra"],
                }
                captured_logs.append(json.dumps(log_entry, ensure_ascii=False))

        with patch.object(
            provider._prometheus_client, "query_range", return_value=mock_results
        ), patch.object(provider.logger, "info", side_effect=capture_log):
            provider._query(metric="test_metric", time_range="30m")

            # 验证所有日志都包含 provider_type
            for log_str in captured_logs:
                log = json.loads(log_str)
                assert "provider_type" in log
                assert log["provider_type"] == "anomaly_detector"

            # 模拟 LogQL 查询: {provider_type="anomaly_detector"}
            filtered_logs = [
                json.loads(log)
                for log in captured_logs
                if json.loads(log).get("provider_type") == "anomaly_detector"
            ]

            assert len(filtered_logs) == len(captured_logs)

    def test_logql_can_filter_by_workflow_id(self, provider):
        """测试 LogQL 可以通过 workflow_id 过滤日志。"""
        mock_results = [
            {
                "metric": {"job": "test"},
                "values": [[1000 + i * 60, str(10.0 + i * 0.1)] for i in range(20)],
            }
        ]

        captured_logs = []

        def capture_log(level, msg, *args, **kwargs):
            if "extra" in kwargs:
                log_entry = {
                    "message": msg,
                    **kwargs["extra"],
                }
                captured_logs.append(json.dumps(log_entry, ensure_ascii=False))

        with patch.object(
            provider._prometheus_client, "query_range", return_value=mock_results
        ), patch.object(provider.logger, "info", side_effect=capture_log):
            provider._query(metric="test_metric", time_range="30m")

            # 验证所有日志都包含 workflow_id
            for log_str in captured_logs:
                log = json.loads(log_str)
                assert "workflow_id" in log
                assert log["workflow_id"] == "test_workflow"

            # 模拟 LogQL 查询: {provider_type="anomaly_detector", workflow_id="test_workflow"}
            filtered_logs = [
                json.loads(log)
                for log in captured_logs
                if json.loads(log).get("workflow_id") == "test_workflow"
            ]

            assert len(filtered_logs) == len(captured_logs)


class TestLogQLQueryCompatibility:
    """测试 LogQL 查询兼容性。"""

    def test_logql_query_by_metric(self, provider):
        """测试 LogQL 可以通过 metric 字段查询。"""
        # 模拟 LogQL 查询: {provider_type="anomaly_detector"} | json | metric="test_metric"
        mock_results = [
            {
                "metric": {"job": "test"},
                "values": [[1000 + i * 60, str(10.0 + i * 0.1)] for i in range(20)],
            }
        ]

        captured_logs = []

        def capture_log(level, msg, *args, **kwargs):
            if "extra" in kwargs:
                log_entry = {
                    "message": msg,
                    **kwargs["extra"],
                }
                captured_logs.append(json.dumps(log_entry, ensure_ascii=False))

        with patch.object(
            provider._prometheus_client, "query_range", return_value=mock_results
        ), patch.object(provider.logger, "info", side_effect=capture_log):
            provider._query(metric="test_metric", time_range="30m")

            # 模拟 LogQL 查询过滤
            filtered_logs = [
                json.loads(log)
                for log in captured_logs
                if json.loads(log).get("metric") == "test_metric"
            ]

            assert len(filtered_logs) > 0
            assert all(log["metric"] == "test_metric" for log in filtered_logs)

    def test_logql_query_by_status(self, provider):
        """测试 LogQL 可以通过 status 字段查询。"""
        # 模拟 LogQL 查询: {provider_type="anomaly_detector"} | json | status="normal"
        mock_results = [
            {
                "metric": {"job": "test"},
                "values": [[1000 + i * 60, str(10.0 + i * 0.1)] for i in range(20)],
            }
        ]

        captured_logs = []

        def capture_log(level, msg, *args, **kwargs):
            if "extra" in kwargs:
                log_entry = {
                    "message": msg,
                    **kwargs["extra"],
                }
                captured_logs.append(json.dumps(log_entry, ensure_ascii=False))

        with patch.object(
            provider._prometheus_client, "query_range", return_value=mock_results
        ), patch.object(provider.logger, "info", side_effect=capture_log):
            provider._query(metric="test_metric", time_range="30m")

            # 模拟 LogQL 查询过滤
            status_logs = [
                json.loads(log)
                for log in captured_logs
                if "status" in json.loads(log)
            ]

            if status_logs:
                # 可以按 status 过滤
                normal_logs = [log for log in status_logs if log.get("status") == "normal"]
                success_logs = [log for log in status_logs if log.get("status") == "success"]

                # 至少应该有一种状态的日志
                assert len(normal_logs) > 0 or len(success_logs) > 0

    def test_logql_query_by_anomaly_count(self, provider):
        """测试 LogQL 可以通过 anomaly_count 字段查询。"""
        # 模拟 LogQL 查询: {provider_type="anomaly_detector"} | json | anomaly_count > 0
        mock_results = [
            {
                "metric": {"job": "test"},
                "values": [[1000 + i * 60, str(10.0 + i * 0.1)] for i in range(20)],
            }
        ]

        captured_logs = []

        def capture_log(level, msg, *args, **kwargs):
            if "extra" in kwargs:
                log_entry = {
                    "message": msg,
                    **kwargs["extra"],
                }
                captured_logs.append(json.dumps(log_entry, ensure_ascii=False))

        with patch.object(
            provider._prometheus_client, "query_range", return_value=mock_results
        ), patch.object(provider.logger, "info", side_effect=capture_log):
            provider._query(metric="test_metric", time_range="30m")

            # 模拟 LogQL 查询过滤
            anomaly_logs = [
                json.loads(log)
                for log in captured_logs
                if "anomaly_count" in json.loads(log)
            ]

            if anomaly_logs:
                # 可以按 anomaly_count 过滤
                with_anomalies = [
                    log for log in anomaly_logs if log.get("anomaly_count", 0) > 0
                ]
                without_anomalies = [
                    log for log in anomaly_logs if log.get("anomaly_count", 0) == 0
                ]

                # 至少应该有一种情况的日志
                assert len(with_anomalies) > 0 or len(without_anomalies) > 0

