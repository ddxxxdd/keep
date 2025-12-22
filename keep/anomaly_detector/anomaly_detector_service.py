"""
Anomaly Detector Service for Keep Platform.

This service automatically detects anomalies in Prometheus metrics
and creates alerts in the Keep platform.

Uses machine learning algorithms (Isolation Forest, Z-Score, Rate Change) 
to detect anomalies and trigger alerts.
"""
import datetime
import hashlib
import logging
import re
import threading
import time
from collections import defaultdict
from typing import Dict, List, Optional, Set

import numpy as np
import requests
from requests.auth import HTTPBasicAuth

from keep.anomaly_detector.algorithms import (
    DetectionResult,
    get_detector,
)
from keep.anomaly_detector.config import AnomalyDetectorConfig, get_config

logger = logging.getLogger(__name__)


class PrometheusClient:
    """Client for interacting with Prometheus API."""
    
    def __init__(self, config: AnomalyDetectorConfig):
        """Initialize the Prometheus client."""
        self.config = config
        self.base_url = config.prometheus.url.rstrip("/")
        self.auth = None
        if config.prometheus.username and config.prometheus.password:
            self.auth = HTTPBasicAuth(
                config.prometheus.username,
                config.prometheus.password
            )
        self.verify_ssl = config.prometheus.verify_ssl
        
    def _make_request(self, endpoint: str, params: dict) -> dict:
        """Make a request to Prometheus API."""
        url = f"{self.base_url}{endpoint}"
        try:
            response = requests.get(
                url,
                params=params,
                auth=self.auth,
                verify=self.verify_ssl,
                timeout=30
            )
            response.raise_for_status()
            return response.json()
        except requests.exceptions.RequestException as e:
            logger.error(f"Prometheus request failed: {e}")
            raise
    
    def get_metric_names(self) -> List[str]:
        """Get all available metric names from Prometheus."""
        try:
            result = self._make_request("/api/v1/label/__name__/values", {})
            if result.get("status") == "success":
                return result.get("data", [])
            return []
        except Exception as e:
            logger.error(f"Failed to get metric names: {e}")
            return []
    
    def query_range(
        self,
        query: str,
        start: float,
        end: float,
        step: str = "60s"
    ) -> List[Dict]:
        """
        Query Prometheus for a time range.
        
        Args:
            query: PromQL query
            start: Start timestamp (Unix)
            end: End timestamp (Unix)
            step: Query step interval
            
        Returns:
            List of metric results with values
        """
        try:
            result = self._make_request("/api/v1/query_range", {
                "query": query,
                "start": start,
                "end": end,
                "step": step
            })
            
            if result.get("status") == "success":
                return result.get("data", {}).get("result", [])
            return []
        except Exception as e:
            logger.error(f"Failed to query range: {e}")
            return []
    
    def query(self, query: str) -> List[Dict]:
        """
        Query Prometheus for current values.
        
        Args:
            query: PromQL query
            
        Returns:
            List of metric results with values
        """
        try:
            result = self._make_request("/api/v1/query", {"query": query})
            
            if result.get("status") == "success":
                return result.get("data", {}).get("result", [])
            return []
        except Exception as e:
            logger.error(f"Failed to query: {e}")
            return []




class AnomalyDetectorService:
    """
    Service for automatic anomaly detection in Prometheus metrics.
    
    This service:
    1. Discovers metrics from Prometheus
    2. Periodically fetches metric data
    3. Runs anomaly detection algorithms
    4. Creates alerts when anomalies are detected
    5. Stores alert history for export
    """
    
    def __init__(self, config: Optional[AnomalyDetectorConfig] = None):
        """
        Initialize the Anomaly Detector Service.
        
        Args:
            config: Configuration for the service (uses environment variables if not provided)
        """
        self.config = config or get_config()
        self.prometheus_client = PrometheusClient(self.config)
        
        # Get the appropriate detector
        self.detector = get_detector(
            algorithm=self.config.algorithm,
            contamination=self.config.contamination_rate,
            zscore_threshold=self.config.zscore_threshold
        )
        
        # Metric history for tracking data over time
        self.metric_history: Dict[str, List[float]] = defaultdict(list)
        
        # Set of active anomaly fingerprints (to avoid duplicate alerts)
        self.active_anomalies: Set[str] = set()
        
        # Alert history for export (stores all alerts with data source type)
        self.alert_history: List[Dict] = []
        
        # Control flags
        self._stop = False
        self._thread: Optional[threading.Thread] = None
        
        logger.info(
            f"Anomaly Detector Service initialized with algorithm: {self.config.algorithm}, "
            f"detection interval: {self.config.detection_interval}s"
        )
    
    def start(self) -> Optional[threading.Thread]:
        """
        Start the anomaly detection service in a background thread.
        
        Returns:
            The background thread running the service
        """
        if not self.config.enabled:
            logger.info("Anomaly Detector Service is disabled")
            return None
            
        self._stop = False
        self._thread = threading.Thread(
            target=self._run_detection_loop,
            daemon=True,
            name="AnomalyDetectorService"
        )
        self._thread.start()
        logger.info("Anomaly Detector Service started")
        return self._thread
    
    def stop(self):
        """Stop the anomaly detection service."""
        self._stop = True
        if self._thread:
            self._thread.join(timeout=10)
        logger.info("Anomaly Detector Service stopped")
    
    def _run_detection_loop(self):
        """Main detection loop that runs periodically."""
        # Wait for the API to be ready
        time.sleep(10)
        
        while not self._stop:
            try:
                logger.info("Running anomaly detection cycle...")
                # Detect anomalies in Prometheus metrics
                self._detect_anomalies()
                logger.info("Anomaly detection cycle completed")
            except Exception as e:
                logger.exception(f"Error in anomaly detection cycle: {e}")
            
            # Wait for the next cycle
            for _ in range(self.config.detection_interval):
                if self._stop:
                    break
                time.sleep(1)
    
    def _should_include_metric(self, metric_name: str) -> bool:
        """Check if a metric should be included based on include/exclude patterns."""
        # Check exclude patterns
        for pattern in self.config.exclude_metrics:
            if pattern and re.match(pattern, metric_name):
                return False
        
        # Check include patterns (if specified)
        if self.config.include_metrics and any(self.config.include_metrics):
            for pattern in self.config.include_metrics:
                if pattern and re.match(pattern, metric_name):
                    return True
            return False
        
        return True
    
    def _discover_metrics(self) -> List[str]:
        """Discover metrics to monitor from Prometheus."""
        all_metrics = self.prometheus_client.get_metric_names()
        
        # Filter metrics based on include/exclude patterns
        filtered_metrics = [
            m for m in all_metrics 
            if self._should_include_metric(m)
        ]
        
        # Debug: log matching metrics
        if self.config.include_metrics:
            matching = [m for m in all_metrics if any(re.match(p, m) for p in self.config.include_metrics if p)]
            logger.debug(f"Include patterns: {self.config.include_metrics}")
            logger.debug(f"Matching metrics: {matching[:10]}")  # Show first 10 matches
        
        # Limit the number of metrics if configured
        if self.config.max_metrics > 0:
            filtered_metrics = filtered_metrics[:self.config.max_metrics]
        
        logger.info(f"Discovered {len(filtered_metrics)} metrics to monitor (from {len(all_metrics)} total)")
        if len(filtered_metrics) == 0 and self.config.include_metrics:
            logger.warning(f"No metrics matched include patterns: {self.config.include_metrics}")
        return filtered_metrics
    
    def _fetch_metric_data(self, metric_name: str) -> np.ndarray:
        """
        Fetch historical data for a metric.
        
        For counter-type metrics (_count, _sum), uses rate() to detect rate changes
        instead of absolute values, which prevents false positives from monotonically
        increasing counters.
        
        Args:
            metric_name: Name of the metric to fetch
            
        Returns:
            Numpy array of metric values aggregated by timestamp
        """
        end_time = time.time()
        start_time = end_time - self.config.query_time_range
        
        # Determine if this is a counter-type metric (cumulative)
        # Counter metrics end with _count, _sum, or _total
        # For these metrics, we should use rate() to detect rate changes
        is_counter = (
            metric_name.endswith('_count') or 
            metric_name.endswith('_sum') or 
            metric_name.endswith('_total') or
            metric_name.endswith('_bucket')  # Histogram buckets are also cumulative
        )
        
        # Build the query: use rate() for counters, raw value for gauges
        # Query all series for the metric without hardcoded filters
        # This allows the algorithm to detect anomalies across all services and status codes
        if is_counter:
            rate_window = "3m"  # use a longer window to ensure enough points for rate()
            # Query all series for counter metrics using rate()
            query = f'rate({metric_name}[{rate_window}])'
            logger.debug(f"Querying counter metric: {query}")
        else:
            # For gauge-type metrics, use raw values
            query = metric_name
            logger.debug(f"Querying gauge metric: {query}")
        
        # Query for the metric
        results = self.prometheus_client.query_range(
            query=query,
            start=start_time,
            end=end_time,
            step=self.config.query_step
        )
        
        if not results:
            return np.array([])
        
        # Aggregate values by timestamp (sum all series for each timestamp)
        # This is more appropriate for time series analysis than merging all values
        from collections import defaultdict
        time_buckets = defaultdict(float)
        
        for series in results:
            values = series.get("values", [])
            for timestamp, value in values:
                try:
                    time_buckets[timestamp] += float(value)
                except (ValueError, TypeError):
                    continue
        
        # Sort by timestamp and return as numpy array
        sorted_times = sorted(time_buckets.items())
        return np.array([value for _, value in sorted_times])
    
    def _update_history(self, metric_name: str, values: np.ndarray):
        """
        Update the historical data for a metric.
        
        Args:
            metric_name: Name of the metric
            values: New values to add
        """
        history = self.metric_history[metric_name]
        history.extend(values.tolist())
        
        # Keep only the last N values
        if len(history) > self.config.history_size:
            self.metric_history[metric_name] = history[-self.config.history_size:]
    
    def _detect_anomalies(self):
        """Run anomaly detection on all monitored metrics."""
        from keep.anomaly_detector.algorithms import AnomalyResult
        
        metrics = self._discover_metrics()
        
        for metric_name in metrics:
            try:
                # Fetch data (contains last 1 hour of data points)
                values = self._fetch_metric_data(metric_name)
                
                logger.info(f"Checking {metric_name}: collected {len(values)} data points")
                if len(values) < self.config.min_data_points:
                    logger.info(
                        f"Skipping {metric_name}: not enough data points "
                        f"({len(values)} < {self.config.min_data_points})"
                    )
                    continue
                
                # Use sliding window approach: use first 70% as baseline, detect on last 30%
                # This separates historical baseline from current detection window
                # This prevents normal historical fluctuations from triggering alerts
                history = values
                
                # Exclude startup period (first 30% of data) to avoid false positives from system startup
                # This prevents alerts triggered by normal system initialization after restart
                # Increased to 30% to ensure stable baseline after system restart
                startup_exclusion_ratio = 0.3  # Exclude first 30% as startup period
                startup_exclusion_size = max(1, int(len(history) * startup_exclusion_ratio))
                stable_history = history[startup_exclusion_size:]
                
                # Log startup exclusion for debugging
                if startup_exclusion_size > 0:
                    logger.debug(
                        f"Excluding {startup_exclusion_size} startup data points ({startup_exclusion_ratio*100:.0f}%) "
                        f"for {metric_name}, using {len(stable_history)} stable data points"
                    )
                
                # Improved baseline calculation: use more data points for better accuracy
                # Reserve at least 10 points for detection, but use more if available
                min_detection_points = 10
                if len(stable_history) < self.config.min_data_points + min_detection_points:
                    # Not enough stable data after excluding startup period
                    logger.debug(
                        f"Skipping {metric_name}: not enough stable data after excluding startup period "
                        f"({len(stable_history)} < {self.config.min_data_points + min_detection_points}, "
                        f"startup exclusion: {startup_exclusion_size} points)"
                    )
                    continue
                
                # Use 80% of stable history for baseline, detect on last 20%
                # This gives more data for baseline (more stable) and smaller detection window
                # (fault data moves out of detection window faster)
                baseline_size = max(
                    self.config.min_data_points,
                    int(len(stable_history) * 0.8)
                )
                # But always reserve at least min_detection_points for detection
                baseline_size = min(baseline_size, len(stable_history) - min_detection_points)
                
                if len(stable_history) > baseline_size:
                    baseline_raw = stable_history[:baseline_size]
                    detection_window = stable_history[baseline_size:]
                    
                    # Filter out outliers from baseline using IQR (Interquartile Range) method
                    # This prevents baseline from being contaminated by early anomalies
                    if len(baseline_raw) > 4:  # Need at least 4 points for IQR
                        Q1 = np.percentile(baseline_raw, 25)
                        Q3 = np.percentile(baseline_raw, 75)
                        IQR = Q3 - Q1
                        if IQR > 0:  # Only filter if there's variation
                            lower_bound = Q1 - 1.5 * IQR
                            upper_bound = Q3 + 1.5 * IQR
                            baseline = baseline_raw[(baseline_raw >= lower_bound) & (baseline_raw <= upper_bound)]
                            if len(baseline) < len(baseline_raw) * 0.5:
                                # If filtering removes more than 50% of data, use original
                                # This handles cases where data is naturally highly variable
                                baseline = baseline_raw
                                logger.debug(f"Outlier filtering removed too much data for {metric_name}, using original baseline")
                            else:
                                logger.debug(f"Filtered {len(baseline_raw) - len(baseline)} outliers from baseline for {metric_name}")
                        else:
                            baseline = baseline_raw
                    else:
                        baseline = baseline_raw
                    
                    # Use median for baseline to be more robust against outliers
                    # This prevents baseline from being contaminated by early anomalies
                    baseline_median = np.median(baseline)
                    baseline_mean = np.mean(baseline)
                    
                    # Use the larger of median or mean to avoid false positives
                    # But prefer median as it's more robust
                    baseline_value = max(baseline_median, baseline_mean * 0.8)
                    
                    # Special handling for zero baseline (e.g., 5xx errors normally at 0)
                    # When baseline is zero, any non-zero value is an anomaly
                    if baseline_value == 0:
                        logger.debug(f"Baseline is zero for {metric_name}, detecting any non-zero values as anomalies")
                        anomalies_in_window = []
                        for i, value in enumerate(detection_window):
                            if value > 0:  # Any non-zero value is an anomaly when baseline is 0
                                anomalies_in_window.append({
                                    'index': baseline_size + i,
                                    'value': float(value),
                                    'change_ratio': 999.0  # Large value to indicate anomaly from 0
                                })
                    else:
                        # Detect anomalies based on rate-of-change (percentage increase vs baseline)
                        # Lower threshold for more sensitive detection
                        threshold_value = baseline_value * (1 + self.config.rate_change_threshold)
                        anomalies_in_window = []
                        for i, value in enumerate(detection_window):
                            if value > threshold_value:
                                anomalies_in_window.append({
                                    'index': baseline_size + i,
                                    'value': float(value),
                                    'change_ratio': float((value - baseline_value) / baseline_value)
                                })
                    
                    if anomalies_in_window:
                        # Check for recent anomalies (last 5 points of detection window)
                        recent_window_size = min(5, len(detection_window))
                        recent_anomalies_list = [
                            a for a in anomalies_in_window 
                            if a['index'] >= len(stable_history) - recent_window_size
                        ]
                        
                        if len(recent_anomalies_list) >= 1:
                            logger.info(
                                f"Detected {len(recent_anomalies_list)} recent anomalies in {metric_name} "
                                f"(baseline_value: {baseline_value:.4f}, threshold: {threshold_value:.4f}, "
                                f"baseline: {len(baseline)} points, detection window: {len(detection_window)} points, "
                                f"window_values: min={np.min(detection_window):.4f} max={np.max(detection_window):.4f}, "
                                f"anomaly_values: {[a['value'] for a in recent_anomalies_list]}, "
                                f"rate_change_threshold: {self.config.rate_change_threshold})"
                            )
                            
                            # Create AnomalyResult objects for alerting
                            anomaly_results = [
                                AnomalyResult(
                                    index=a['index'],
                                    value=a['value'],
                                    is_anomaly=True,
                                    score=a.get('change_ratio', 0.0),
                                    method="rate_change",
                                    details={
                                        "baseline_median": float(baseline_median),
                                        "baseline_mean": float(baseline_mean),
                                        "baseline_value": float(baseline_value),
                                        "rate_change_threshold": float(self.config.rate_change_threshold),
                                        "startup_exclusion": startup_exclusion_size
                                    }
                                )
                                for a in recent_anomalies_list
                            ]
                            
                            # Create DetectionResult for alerting
                            result = DetectionResult(
                                metric_name=metric_name,
                                anomalies=anomaly_results,
                                total_points=len(history),
                                anomaly_count=len(recent_anomalies_list),
                                mean=float(baseline_mean),
                                std=float(np.std(baseline)),
                                algorithm="rate_change"
                            )
                            self._create_alert("metrics", metric_name, result, anomaly_results)
                        else:
                            logger.debug(
                                f"Ignored anomalies in {metric_name}: "
                                f"only {len(recent_anomalies_list)} recent anomalies, need at least 1"
                            )
                    else:
                        logger.debug(f"No anomalies detected in {metric_name} detection window")
                else:
                    logger.info(
                        f"Skipping {metric_name}: not enough stable data for sliding window "
                        f"({len(stable_history)} < {baseline_size}, startup exclusion: {startup_exclusion_size} points)"
                    )
                    continue
                        
            except Exception as e:
                logger.error(f"Error processing metric {metric_name}: {e}")
    
    def _generate_fingerprint(self, source_type: str, identifier: str) -> str:
        """Generate a unique fingerprint for an anomaly alert."""
        fingerprint_src = f"anomaly-detector:{source_type}:{identifier}"
        return hashlib.md5(fingerprint_src.encode()).hexdigest()
    
    def _create_alert(
        self,
        source_type: str,
        identifier: str,
        result: DetectionResult,
        recent_anomalies: list
    ):
        """
        Create an alert for detected anomalies.
        
        Args:
            source_type: Type of data source (always "metrics" for now)
            identifier: Metric name
            result: Detection result
            recent_anomalies: List of recent anomalies
        """
        logger.info(f"Creating alert for {source_type}:{identifier} with {len(recent_anomalies)} anomalies")
        
        fingerprint = self._generate_fingerprint(source_type, identifier)
        
        # Check if we already have an active alert for this
        if fingerprint in self.active_anomalies:
            # Verify with Keep API if alert really exists
            # This handles the case where alert was manually deleted from Keep UI
            alert_exists = self._check_alert_exists_in_keep(fingerprint)
            if alert_exists:
                logger.info(f"Alert already active for {source_type}:{identifier}, skipping")
                return
            else:
                # Alert was deleted from Keep, remove from active_anomalies and continue
                logger.info(
                    f"Alert {fingerprint} not found in Keep API (may have been manually deleted), "
                    f"removing from active_anomalies and creating new alert"
                )
                self.active_anomalies.discard(fingerprint)
        
        # Get detailed information from the first anomaly
        first_anomaly = recent_anomalies[0]
        latest_anomaly = recent_anomalies[-1]
        anomaly_details = first_anomaly.details if hasattr(first_anomaly, 'details') and first_anomaly.details else {}
        
        # Calculate change percentage
        baseline_value = anomaly_details.get('baseline_value', result.mean)
        change_percentage = latest_anomaly.score * 100 if latest_anomaly.score else 0
        
        # Get current timestamp for tracking
        detection_time = datetime.datetime.now(tz=datetime.timezone.utc)
        detection_time_str = detection_time.strftime("%Y-%m-%d %H:%M:%S UTC")
        
        # Create source-specific alert details
        source_labels = {
            "metrics": {
                "alertname": f"AnomalyDetected_Metrics_{identifier.replace('.', '_')}",
                "severity": "warning",
                "metric": identifier,
                "data_source": "metrics",
                "detection_method": result.algorithm,
                "source": "anomaly-detector"
            },
            "traces": {
                "alertname": f"AnomalyDetected_Traces_{identifier.replace('.', '_')}",
                "severity": "warning",
                "service": identifier,
                "data_source": "traces",
                "detection_method": result.algorithm,
                "source": "anomaly-detector"
            },
            "logs": {
                "alertname": f"AnomalyDetected_Logs_{identifier.replace('.', '_')}",
                "severity": "warning",
                "log_source": identifier,
                "data_source": "logs",
                "detection_method": result.algorithm,
                "source": "anomaly-detector"
            }
        }
        
        source_descriptions = {
            "metrics": (
                f"AI 异常检测算法发现指标 {identifier} 的值出现异常。\n\n"
                f"【数据源类型】Metrics (指标数据)\n"
                f"【检测时间】{detection_time_str}\n"
                f"【检测算法】{result.algorithm}\n"
                f"【异常数量】检测到 {len(recent_anomalies)} 个异常点\n\n"
                f"【基线信息】\n"
                f"  基线中位数: {anomaly_details.get('baseline_median', 0):.4f}\n"
                f"  基线平均值: {anomaly_details.get('baseline_mean', result.mean):.4f}\n"
                f"  基线值: {baseline_value:.4f}\n"
                f"  标准差: {result.std:.4f}\n\n"
                f"【当前异常】\n"
                f"  最新异常值: {latest_anomaly.value:.4f}\n"
                f"  变化比例: {change_percentage:.1f}%\n"
                f"  异常分数: {latest_anomaly.score:.4f}\n"
                f"  阈值: {anomaly_details.get('rate_change_threshold', 0.1) * 100:.1f}%\n\n"
                f"【提示】请对比故障注入开启时间来判断是否为手动注入产生的异常"
            ),
            "traces": (
                f"AI 异常检测算法发现追踪数据 {identifier} 出现异常。\n\n"
                f"【数据源类型】Traces (追踪数据)\n"
                f"【检测时间】{detection_time_str}\n"
                f"【检测算法】{result.algorithm}\n"
                f"【异常数量】检测到 {len(recent_anomalies)} 个异常点\n\n"
                f"【基线信息】\n"
                f"  基线平均值: {result.mean:.4f}\n"
                f"  标准差: {result.std:.4f}\n\n"
                f"【当前异常】\n"
                f"  最新异常值: {latest_anomaly.value:.4f}\n"
                f"  变化比例: {change_percentage:.1f}%\n"
                f"  异常分数: {latest_anomaly.score:.4f}\n\n"
                f"【提示】请对比故障注入开启时间来判断是否为手动注入产生的异常"
            ),
            "logs": (
                f"AI 异常检测算法发现日志数据 {identifier} 出现异常。\n\n"
                f"【数据源类型】Logs (日志数据)\n"
                f"【检测时间】{detection_time_str}\n"
                f"【检测算法】{result.algorithm}\n"
                f"【异常数量】检测到 {len(recent_anomalies)} 个异常点\n\n"
                f"【基线信息】\n"
                f"  基线平均值: {result.mean:.4f}\n"
                f"  标准差: {result.std:.4f}\n\n"
                f"【当前异常】\n"
                f"  最新异常值: {latest_anomaly.value:.4f}\n"
                f"  变化比例: {change_percentage:.1f}%\n"
                f"  异常分数: {latest_anomaly.score:.4f}\n\n"
                f"【提示】请对比故障注入开启时间来判断是否为手动注入产生的异常"
            )
        }
        
        # Create alert payload with detailed context
        alert_payload = {
            "alerts": [{
                "status": "firing",
                "labels": source_labels.get(source_type, source_labels["metrics"]),
                "annotations": {
                    "summary": f"异常检测：{source_type} 数据源 {identifier} 出现异常模式",
                    "description": source_descriptions.get(source_type, "")
                },
                "startsAt": detection_time.isoformat(),
                "fingerprint": fingerprint,
                "generatorURL": f"{self.config.prometheus.url}/graph" if source_type == "metrics" else ""
            }]
        }
        
        # Store alert in history for export
        alert_record = {
            "timestamp": detection_time.isoformat(),
            "source_type": source_type,
            "identifier": identifier,
            "fingerprint": fingerprint,
            "anomaly_count": len(recent_anomalies),
            "algorithm": result.algorithm,
            "baseline_value": float(baseline_value),
            "latest_anomaly_value": float(latest_anomaly.value),
            "change_percentage": float(change_percentage),
            "details": anomaly_details,
            "alert_payload": alert_payload
        }
        self.alert_history.append(alert_record)
        
        # Keep only last 1000 alerts in memory
        if len(self.alert_history) > 1000:
            self.alert_history = self.alert_history[-1000:]
        
        # Send to Keep
        self._send_alert_to_keep(alert_payload, fingerprint)
    
    def _check_alert_exists_in_keep(self, fingerprint: str) -> bool:
        """
        Check if an alert exists in Keep API by fingerprint.
        
        Args:
            fingerprint: Alert fingerprint
            
        Returns:
            True if alert exists and is active, False otherwise
        """
        keep_url = f"{self.config.keep_api_url}/alerts/{fingerprint}"
        
        headers = {}
        
        # Add API key if configured
        if self.config.keep_api_key:
            headers["X-API-KEY"] = self.config.keep_api_key
        
        try:
            response = requests.get(
                keep_url,
                headers=headers,
                timeout=10
            )
            
            if response.status_code == 200:
                # Alert exists, check if it's still active (status != "resolved")
                alert_data = response.json()
                event = alert_data.get("event", {})
                status = event.get("status", "")
                if status != "resolved":
                    logger.debug(f"Alert {fingerprint} exists and is active in Keep")
                    return True
                else:
                    logger.debug(f"Alert {fingerprint} exists but is resolved in Keep")
                    return False
            elif response.status_code == 404:
                # Alert doesn't exist
                logger.debug(f"Alert {fingerprint} not found in Keep API")
                return False
            else:
                # Other error, assume alert exists to be safe
                logger.warning(
                    f"Failed to check alert in Keep: {response.status_code} - {response.text}, "
                    f"assuming alert exists"
                )
                return True
                
        except requests.exceptions.RequestException as e:
            # Network error, assume alert exists to be safe
            logger.warning(f"Failed to check alert in Keep: {e}, assuming alert exists")
            return True
    
    def _send_alert_to_keep(self, alert_payload: dict, fingerprint: str):
        """
        Send an alert to the Keep platform.
        
        Args:
            alert_payload: Alert payload in Prometheus Alertmanager format
            fingerprint: Alert fingerprint
        """
        keep_url = f"{self.config.keep_api_url}/alerts/event/prometheus"
        
        headers = {
            "Content-Type": "application/json",
        }
        
        # Add API key if configured
        if self.config.keep_api_key:
            headers["X-API-KEY"] = self.config.keep_api_key
        
        try:
            response = requests.post(
                keep_url,
                json=alert_payload,
                headers=headers,
                timeout=30
            )
            
            if response.status_code in (200, 201, 202):
                logger.info(f"Alert sent to Keep successfully: {fingerprint}")
                self.active_anomalies.add(fingerprint)
            else:
                logger.error(
                    f"Failed to send alert to Keep: {response.status_code} - {response.text}"
                )
                
        except requests.exceptions.RequestException as e:
            logger.error(f"Failed to send alert to Keep: {e}")
    
    def resolve_alert(self, source_type: str, identifier: str):
        """
        Resolve an active alert.
        
        Args:
            source_type: Type of data source ("metrics", "traces", "logs")
            identifier: Identifier for the data
        """
        fingerprint = self._generate_fingerprint(source_type, identifier)
        
        if fingerprint not in self.active_anomalies:
            return
        
        # Create resolved alert payload
        alert_payload = {
            "alerts": [{
                "status": "resolved",
                "labels": {
                    "alertname": f"AnomalyDetected_{source_type.capitalize()}_{identifier.replace('.', '_')}",
                    "severity": "warning",
                    "data_source": source_type,
                    "source": "anomaly-detector"
                },
                "annotations": {
                    "summary": f"异常恢复：{source_type} 数据源 {identifier} 已恢复正常",
                    "description": f"{source_type} 数据源 {identifier} 不再检测到异常模式。"
                },
                "endsAt": datetime.datetime.now(tz=datetime.timezone.utc).isoformat(),
                "fingerprint": fingerprint
            }]
        }
        
        # Send to Keep
        self._send_alert_to_keep(alert_payload, fingerprint)
        self.active_anomalies.discard(fingerprint)
    
    def export_alerts(
        self,
        start_time: Optional[datetime.datetime] = None,
        end_time: Optional[datetime.datetime] = None,
        source_type: Optional[str] = None,
        format: str = "json"
    ) -> str:
        """
        Export alert history to a file.
        
        Args:
            start_time: Optional start time filter
            end_time: Optional end time filter
            source_type: Optional filter by source type ("metrics", "traces", "logs")
            format: Export format ("json" or "csv")
            
        Returns:
            Path to the exported file
        """
        import os
        import csv
        import json
        
        # Filter alerts
        filtered_alerts = self.alert_history
        
        if start_time:
            filtered_alerts = [
                a for a in filtered_alerts
                if datetime.datetime.fromisoformat(a["timestamp"].replace("Z", "+00:00")) >= start_time
            ]
        
        if end_time:
            filtered_alerts = [
                a for a in filtered_alerts
                if datetime.datetime.fromisoformat(a["timestamp"].replace("Z", "+00:00")) <= end_time
            ]
        
        if source_type:
            filtered_alerts = [
                a for a in filtered_alerts
                if a["source_type"] == source_type
            ]
        
        # Create export directory if it doesn't exist
        export_dir = os.path.join(os.getcwd(), "alert_exports")
        os.makedirs(export_dir, exist_ok=True)
        
        # Generate filename
        timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        filename = f"alerts_export_{timestamp}.{format}"
        filepath = os.path.join(export_dir, filename)
        
        if format == "json":
            # Export as JSON
            with open(filepath, "w", encoding="utf-8") as f:
                json.dump(filtered_alerts, f, indent=2, ensure_ascii=False)
        elif format == "csv":
            # Export as CSV
            if filtered_alerts:
                fieldnames = [
                    "timestamp", "source_type", "identifier", "fingerprint",
                    "anomaly_count", "algorithm", "baseline_value",
                    "latest_anomaly_value", "change_percentage"
                ]
                with open(filepath, "w", newline="", encoding="utf-8") as f:
                    writer = csv.DictWriter(f, fieldnames=fieldnames)
                    writer.writeheader()
                    for alert in filtered_alerts:
                        row = {k: alert.get(k, "") for k in fieldnames}
                        writer.writerow(row)
        
        logger.info(f"Exported {len(filtered_alerts)} alerts to {filepath}")
        return filepath
    
    def get_alert_statistics(self) -> Dict:
        """
        Get statistics about detected alerts.
        
        Returns:
            Dictionary with statistics by source type
        """
        stats = {
            "total_alerts": len(self.alert_history),
            "by_source_type": defaultdict(int),
            "by_algorithm": defaultdict(int),
            "recent_alerts": []
        }
        
        for alert in self.alert_history:
            stats["by_source_type"][alert["source_type"]] += 1
            stats["by_algorithm"][alert["algorithm"]] += 1
        
        # Get last 10 alerts
        stats["recent_alerts"] = [
            {
                "timestamp": a["timestamp"],
                "source_type": a["source_type"],
                "identifier": a["identifier"],
                "anomaly_count": a["anomaly_count"]
            }
            for a in self.alert_history[-10:]
        ]
        
        return stats


# Global service instance for API access
_global_service_instance: Optional[AnomalyDetectorService] = None


def get_anomaly_detector_service() -> Optional[AnomalyDetectorService]:
    """
    Get the global Anomaly Detector Service instance.
    
    Returns:
        The service instance, or None if not initialized
    """
    return _global_service_instance


def launch_anomaly_detector_thread(
    config: Optional[AnomalyDetectorConfig] = None
) -> Optional[threading.Thread]:
    """
    Launch the Anomaly Detector Service in a background thread.
    
    This function is meant to be called from server_jobs_bg.py.
    
    Args:
        config: Optional configuration (uses environment variables if not provided)
        
    Returns:
        The background thread running the service, or None if disabled
    """
    global _global_service_instance
    service = AnomalyDetectorService(config)
    _global_service_instance = service
    return service.start()


if __name__ == "__main__":
    # For testing purposes
    import sys
    
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
    )
    
    print("Starting Anomaly Detector Service...")
    print("Press Ctrl+C to stop")
    
    service = AnomalyDetectorService()
    thread = service.start()
    
    if thread:
        try:
            while thread.is_alive():
                thread.join(timeout=1)
        except KeyboardInterrupt:
            print("\nStopping service...")
            service.stop()
    else:
        print("Service is disabled. Set ANOMALY_DETECTOR_ENABLED=true to enable.")

