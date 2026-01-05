"""
Loki 客户端封装。

用于从 Loki 查询日志数据，通过 LogQL 聚合查询返回时间序列数据。
"""

import logging
import time
from typing import List

import numpy as np

from keep.anomaly_detector.config import AnomalyDetectorConfig

logger = logging.getLogger(__name__)


class LokiClient:
    """
    Loki 客户端封装。

    使用 LogQL 聚合查询（如 rate(), count_over_time()）直接返回时间序列数据，
    与 Prometheus 查询格式兼容，便于复用现有的时间序列处理逻辑。
    """

    def __init__(self, config: AnomalyDetectorConfig):
        """根据异常检测配置初始化 Loki 访问参数。"""
        import requests  # 延迟导入，避免模块加载时的硬依赖

        self._requests = requests
        self.config = config
        self.base_url = config.loki.url.rstrip("/")

    def _make_request(self, endpoint: str, params: dict) -> dict:
        """向 Loki API 发起 HTTP 请求，封装基础错误处理。"""
        url = f"{self.base_url}{endpoint}"
        try:
            response = self._requests.get(
                url,
                params=params,
                timeout=30,
            )
            response.raise_for_status()
            return response.json()
        except self._requests.exceptions.RequestException as exc:  # type: ignore[attr-defined]
            logger.error(f"Loki request failed: {exc}")
            raise

    def query_range(
        self,
        query: str,
        start: float,
        end: float,
        step: str = "60s",
    ) -> np.ndarray:
        """
        按时间范围执行 LogQL 聚合查询并返回时间序列。

        Args:
            query: LogQL 查询语句（如 'sum(rate({job="varlogs"}[5m])) by (level)'）
            start: 开始时间（Unix 时间戳，秒）
            end: 结束时间（Unix 时间戳，秒）
            step: 时间步长（如 "60s", "5m"）

        Returns:
            时间序列数据（numpy.ndarray）
        """
        try:
            # 解析步长为秒数
            step_seconds = self._parse_step(step)

            # 调用 Loki query_range API
            result = self._make_request(
                "/loki/api/v1/query_range",
                {
                    "query": query,
                    "start": int(start),
                    "end": int(end),
                    "step": f"{step_seconds}s",
                },
            )

            # 解析 Loki 返回的时间序列数据
            data = result.get("data", {})
            result_list = data.get("result", [])

            if not result_list:
                return np.array([])

            # 聚合多条时间序列为单条（按时间戳求和）
            from collections import defaultdict

            time_buckets: dict[float, float] = defaultdict(float)

            for series in result_list:
                values = series.get("values", [])
                for timestamp_str, value_str in values:
                    try:
                        # Loki 返回的时间戳是纳秒级，需要转换为秒
                        timestamp = float(timestamp_str) / 1e9
                        value = float(value_str)
                        time_buckets[timestamp] += value
                    except (ValueError, TypeError):
                        continue

            if not time_buckets:
                return np.array([])

            # 转换为按时间排序的数值数组
            sorted_items = sorted(time_buckets.items())
            values = [v for _, v in sorted_items]

            return np.array(values, dtype=float)

        except Exception as e:
            logger.error(f"Loki query_range failed: {e}")
            return np.array([])

    @staticmethod
    def _parse_step(step: str) -> int:
        """解析步长字符串为秒数（如 "60s" -> 60, "5m" -> 300）。"""
        import re

        match = re.match(r"(\d+)([smhd])$", step.strip().lower())
        if not match:
            return 60  # 默认 60 秒

        amount, unit = match.groups()
        amount = int(amount)

        factor = {"s": 1, "m": 60, "h": 3600, "d": 86400}.get(unit, 60)
        return amount * factor

