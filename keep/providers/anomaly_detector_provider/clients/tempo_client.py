"""
Tempo 客户端封装。

用于从 Tempo 查询 trace 数据，并通过 TraceQL 聚合为时间序列数据。
"""

import logging
import time
from typing import List, Optional

import numpy as np

from keep.anomaly_detector.config import AnomalyDetectorConfig

logger = logging.getLogger(__name__)


class TempoClient:
    """
    Tempo 客户端封装。

    使用 TraceQL 查询 trace 数据，然后按时间窗口聚合为时间序列（numpy.ndarray），
    供异常检测算法使用。
    """

    def __init__(self, config: AnomalyDetectorConfig):
        """根据异常检测配置初始化 Tempo 访问参数。"""
        import requests  # 延迟导入，避免模块加载时的硬依赖

        self._requests = requests
        self.config = config
        self.base_url = config.tempo.url.rstrip("/")

    def _make_request(self, endpoint: str, params: dict) -> dict:
        """向 Tempo API 发起 HTTP 请求，封装基础错误处理。"""
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
            logger.error(f"Tempo request failed: {exc}")
            raise

    def query_traces(
        self,
        query: str,
        start: float,
        end: float,
        limit: int = 1000,
    ) -> List[dict]:
        """
        使用 TraceQL 查询 trace 数据。

        Args:
            query: TraceQL 查询语句（如 '{ .service_name = "frontend" }'）
            start: 开始时间（Unix 时间戳，秒）
            end: 结束时间（Unix 时间戳，秒）
            limit: 返回的最大 trace 数量

        Returns:
            Trace 数据列表
        """
        try:
            result = self._make_request(
                "/api/search",
                {
                    "q": query,
                    "start": int(start),
                    "end": int(end),
                    "limit": limit,
                },
            )
            return result.get("traces", [])
        except Exception as e:
            logger.error(f"Tempo query failed: {e}")
            return []

    def query_range(
        self,
        query: str,
        start: float,
        end: float,
        step: str = "60s",
    ) -> np.ndarray:
        """
        按时间范围执行 TraceQL 查询并聚合为时间序列。

        该方法会将 trace 数据按时间窗口聚合为数值序列（例如：每 5 分钟的 trace 数量），
        返回 numpy.ndarray 格式的时间序列数据。

        Args:
            query: TraceQL 查询语句
            start: 开始时间（Unix 时间戳，秒）
            end: 结束时间（Unix 时间戳，秒）
            step: 时间步长（如 "60s", "5m"）

        Returns:
            时间序列数据（numpy.ndarray）
        """
        # 解析步长
        step_seconds = self._parse_step(step)

        # 查询 trace 数据
        traces = self.query_traces(query, start, end)

        if not traces:
            return np.array([])

        # 按时间窗口聚合 trace 数量
        time_buckets: dict[float, int] = {}
        current_time = start

        while current_time < end:
            time_buckets[current_time] = 0
            current_time += step_seconds

        # 统计每个时间窗口内的 trace 数量
        for trace in traces:
            # Tempo 返回的 trace 通常包含 startTimeUnixNano 字段
            # 转换为秒级时间戳
            trace_time = trace.get("startTimeUnixNano", 0) / 1e9
            if start <= trace_time <= end:
                # 找到对应的时间窗口
                bucket_time = start + int((trace_time - start) / step_seconds) * step_seconds
                time_buckets[bucket_time] = time_buckets.get(bucket_time, 0) + 1

        # 转换为按时间排序的数值数组
        sorted_times = sorted(time_buckets.keys())
        values = [time_buckets[t] for t in sorted_times]

        return np.array(values, dtype=float)

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

