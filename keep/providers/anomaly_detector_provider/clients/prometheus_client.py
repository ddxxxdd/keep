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

