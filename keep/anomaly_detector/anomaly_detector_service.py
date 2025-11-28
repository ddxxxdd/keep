"""
Anomaly Detector Service for Keep Platform.

This service automatically detects anomalies in Prometheus metrics using
machine learning algorithms (Isolation Forest, Z-Score) and creates alerts
in the Keep platform.
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
    1. Discovers all metrics from Prometheus
    2. Periodically fetches metric data
    3. Runs anomaly detection algorithms
    4. Creates alerts when anomalies are detected
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
        
        # Limit the number of metrics if configured
        if self.config.max_metrics > 0:
            filtered_metrics = filtered_metrics[:self.config.max_metrics]
        
        logger.info(f"Discovered {len(filtered_metrics)} metrics to monitor (from {len(all_metrics)} total)")
        return filtered_metrics
    
    def _fetch_metric_data(self, metric_name: str) -> np.ndarray:
        """
        Fetch historical data for a metric.
        
        Args:
            metric_name: Name of the metric to fetch
            
        Returns:
            Numpy array of metric values aggregated by timestamp
        """
        end_time = time.time()
        start_time = end_time - self.config.query_time_range
        
        # Query for the metric
        results = self.prometheus_client.query_range(
            query=metric_name,
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
                
                # Use current query data directly for detection
                # Each query already contains sufficient historical data (1 hour)
                # This avoids duplicate data accumulation issues
                history = values
                
                # Run detection on the current data
                result = self.detector.detect(history, metric_name)
                
                if result.anomalies:
                    # Check for recent anomalies (last N data points)
                    lookback_count = min(5, len(history))
                    recent_threshold = max(0, len(history) - lookback_count)
                    recent_anomalies = [
                        a for a in result.anomalies 
                        if a.index >= recent_threshold
                    ]
                    
                    if recent_anomalies:
                        logger.info(
                            f"Detected {len(recent_anomalies)} anomalies in {metric_name} "
                            f"(algorithm: {result.algorithm}, total points: {len(history)}, "
                            f"lookback: {lookback_count}, all anomalies: {len(result.anomalies)})"
                        )
                        self._create_alert(metric_name, result, recent_anomalies)
                    else:
                        last_anomaly_index = result.anomalies[-1].index
                        logger.info(
                            f"Ignored anomalies in {metric_name}: "
                            f"last anomaly index={last_anomaly_index}, required >= {recent_threshold}"
                        )
                        
            except Exception as e:
                logger.error(f"Error processing metric {metric_name}: {e}")
    
    def _generate_fingerprint(self, metric_name: str) -> str:
        """Generate a unique fingerprint for an anomaly alert."""
        fingerprint_src = f"anomaly-detector:{metric_name}"
        return hashlib.md5(fingerprint_src.encode()).hexdigest()
    
    def _create_alert(
        self,
        metric_name: str,
        result: DetectionResult,
        recent_anomalies: list
    ):
        """
        Create an alert for detected anomalies.
        
        Args:
            metric_name: Name of the metric with anomalies
            result: Detection result
            recent_anomalies: List of recent anomalies
        """
        fingerprint = self._generate_fingerprint(metric_name)
        
        # Check if we already have an active alert for this metric
        if fingerprint in self.active_anomalies:
            logger.debug(f"Alert already active for {metric_name}, skipping")
            return
        
        # Create alert payload
        alert_payload = {
            "alerts": [{
                "status": "firing",
                "labels": {
                    "alertname": f"AnomalyDetected_{metric_name.replace('.', '_')}",
                    "severity": "warning",
                    "metric": metric_name,
                    "detection_method": result.algorithm,
                    "source": "anomaly-detector"
                },
                "annotations": {
                    "summary": f"异常检测：指标 {metric_name} 出现异常模式",
                    "description": (
                        f"AI 异常检测算法发现指标 {metric_name} 的值出现异常。\n\n"
                        f"检测算法: {result.algorithm}\n"
                        f"检测到 {len(recent_anomalies)} 个异常点\n"
                        f"平均值: {result.mean:.2f}\n"
                        f"标准差: {result.std:.2f}\n"
                        f"最新异常值: {recent_anomalies[-1].value:.2f}\n"
                        f"异常分数: {recent_anomalies[-1].score:.2f}"
                    )
                },
                "startsAt": datetime.datetime.now(tz=datetime.timezone.utc).isoformat(),
                "fingerprint": fingerprint,
                "generatorURL": f"{self.config.prometheus.url}/graph?g0.expr={metric_name}"
            }]
        }
        
        # Send to Keep
        self._send_alert_to_keep(alert_payload, fingerprint)
    
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
    
    def resolve_alert(self, metric_name: str):
        """
        Resolve an active alert for a metric.
        
        Args:
            metric_name: Name of the metric to resolve
        """
        fingerprint = self._generate_fingerprint(metric_name)
        
        if fingerprint not in self.active_anomalies:
            return
        
        # Create resolved alert payload
        alert_payload = {
            "alerts": [{
                "status": "resolved",
                "labels": {
                    "alertname": f"AnomalyDetected_{metric_name.replace('.', '_')}",
                    "severity": "warning",
                    "metric": metric_name,
                    "source": "anomaly-detector"
                },
                "annotations": {
                    "summary": f"异常恢复：指标 {metric_name} 已恢复正常",
                    "description": f"指标 {metric_name} 不再检测到异常模式。"
                },
                "endsAt": datetime.datetime.now(tz=datetime.timezone.utc).isoformat(),
                "fingerprint": fingerprint
            }]
        }
        
        # Send to Keep
        self._send_alert_to_keep(alert_payload, fingerprint)
        self.active_anomalies.discard(fingerprint)


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
    service = AnomalyDetectorService(config)
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

