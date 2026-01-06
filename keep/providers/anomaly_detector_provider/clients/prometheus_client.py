"""
Prometheus 客户端封装。

用于从 Prometheus 查询时间序列数据，支持基本认证和 SSL 验证。
"""

import logging

from keep.anomaly_detector.config import AnomalyDetectorConfig

logger = logging.getLogger(__name__)


class PrometheusClient:
    """
    Prometheus 客户端封装。

    这里直接复用原异常检测服务中的访问方式，但只保留 HTTP 请求与基础错误处理逻辑，
    方便在 Provider 中按需调用。
    """

    def __init__(self, config: AnomalyDetectorConfig):
        """根据异常检测配置初始化 Prometheus 访问参数。"""
        from requests.auth import HTTPBasicAuth
        import requests  # 延迟导入，避免模块加载时的硬依赖

        self._requests = requests
        self.config = config
        self.base_url = config.prometheus.url.rstrip("/")
        self.auth = None
        if config.prometheus.username and config.prometheus.password:
            self.auth = HTTPBasicAuth(
                config.prometheus.username,
                config.prometheus.password,
            )
        self.verify_ssl = config.prometheus.verify_ssl

    def _make_request(self, endpoint: str, params: dict) -> dict:
        """向 Prometheus API 发起 HTTP 请求，封装基础错误处理。"""
        url = f"{self.base_url}{endpoint}"
        try:
            response = self._requests.get(
                url,
                params=params,
                auth=self.auth,
                verify=self.verify_ssl,
                timeout=30,
            )
            response.raise_for_status()
            return response.json()
        except self._requests.exceptions.RequestException as exc:  # type: ignore[attr-defined]
            logger.error(f"Prometheus request failed: {exc}")
            raise

    def query_range(
        self,
        query: str,
        start: float,
        end: float,
        step: str = "60s",
    ) -> list[dict]:
        """
        按时间范围执行 PromQL 查询。

        返回值为 Prometheus 原始 JSON 中的 `data.result` 列表，
        每个元素都包含 `values` 字段。
        """
        try:
            result = self._make_request(
                "/api/v1/query_range",
                {
                    "query": query,
                    "start": start,
                    "end": end,
                    "step": step,
                },
            )
        except Exception:
            return []

        if result.get("status") != "success":
            return []
        return result.get("data", {}).get("result", [])

    def list_metrics(
        self,
        include_patterns: list[str] | None = None,
        exclude_patterns: list[str] | None = None,
        max_metrics: int = 100,
    ) -> list[str]:
        """
        列出 Prometheus 中的指标名称，并根据包含/排除规则和数量上限做过滤。

        - 默认从 /api/v1/label/__name__/values 端点获取所有指标名
        - include_patterns: 正则或前缀模式列表；为空则表示不过滤（全量作为候选）
        - exclude_patterns: 正则或前缀模式列表；匹配到的指标会被排除
        - max_metrics: 返回的最大指标数量；<=0 时使用默认上限 100
        """
        include_patterns = include_patterns or []
        exclude_patterns = exclude_patterns or []
        if max_metrics <= 0:
            max_metrics = 100

        try:
            result = self._make_request("/api/v1/label/__name__/values", {})
        except Exception:
            return []

        if result.get("status") != "success":
            return []

        names = result.get("data") or []

        import re

        def _match_any(name: str, patterns: list[str]) -> bool:
            if not patterns:
                return True
            for pattern in patterns:
                if not pattern:
                    continue
                try:
                    if re.search(pattern, name):
                        return True
                except re.error:
                    # 如果不是合法正则，则退化为前缀匹配
                    if name.startswith(pattern):
                        return True
            return False

        def _match_exclude(name: str, patterns: list[str]) -> bool:
            if not patterns:
                return False
            for pattern in patterns:
                if not pattern:
                    continue
                try:
                    if re.search(pattern, name):
                        return True
                except re.error:
                    if name.startswith(pattern):
                        return True
            return False

        filtered: list[str] = []
        for metric_name in names:
            if include_patterns and not _match_any(metric_name, include_patterns):
                continue
            if _match_exclude(metric_name, exclude_patterns):
                continue
            filtered.append(metric_name)
            if len(filtered) >= max_metrics:
                break

        return filtered

