"""
Anomaly Detector Service for Keep Platform.

This service automatically detects anomalies in:
- Prometheus metrics (primary)
- Tempo traces (optional)
- Loki logs (optional)

Uses machine learning algorithms (Isolation Forest, Z-Score, Rate Change) 
and creates alerts in the Keep platform.
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


class TempoClient:
    """Client for interacting with Tempo API for trace anomaly detection."""
    
    def __init__(self, config: AnomalyDetectorConfig):
        """Initialize the Tempo client."""
        self.config = config
        self.base_url = config.tempo.url.rstrip("/")
        
    def _make_request(self, endpoint: str, params: dict, method: str = "GET") -> dict:
        """Make a request to Tempo API."""
        url = f"{self.base_url}{endpoint}"
        try:
            if method == "POST":
                # Try POST with JSON body
                response = requests.post(
                    url,
                    json=params,
                    headers={"Content-Type": "application/json"},
                    timeout=30
                )
            else:
                # Try GET with query parameters
                response = requests.get(url, params=params, timeout=30)
            
            response.raise_for_status()
            return response.json()
        except requests.exceptions.HTTPError as e:
            # Log detailed error information for debugging
            error_msg = str(e)
            if hasattr(e.response, 'text'):
                error_msg += f" - Response: {e.response.text[:200]}"
            logger.debug(f"Tempo HTTP error for {endpoint} with params {params}: {error_msg}")
            # Don't raise - return empty dict to allow graceful degradation
            return {}
        except requests.exceptions.RequestException as e:
            logger.debug(f"Tempo request failed for {endpoint} (non-critical): {e}")
            # Don't raise - return empty dict to allow graceful degradation
            return {}
    
    def query_traces(
        self,
        start: float,
        end: float,
        service_name: Optional[str] = None
    ) -> List[Dict]:
        """
        Query traces from Tempo for a time range using TraceQL.
        
        Args:
            start: Start timestamp (Unix, seconds)
            end: End timestamp (Unix, seconds)
            service_name: Optional service name filter
            
        Returns:
            List of trace summaries with duration and error information
        """
        try:
            # Convert to nanoseconds for Tempo API
            start_ns = int(start * 1e9)
            end_ns = int(end * 1e9)
            
            # Build TraceQL query
            # Tempo uses TraceQL syntax: { service.name = "service" }
            if service_name:
                traceql_query = f'{{ service.name = "{service_name}" }}'
            else:
                # Query all traces
                traceql_query = "{}"
            
            # Try multiple API formats
            result = None
            
            # Method 1: POST with JSON body (Tempo 2.0+ format)
            params_post = {
                "q": traceql_query,
                "start": start_ns,  # As integer, not string
                "end": end_ns,     # As integer, not string
                "limit": 1000
            }
            result = self._make_request("/api/search", params_post, method="POST")
            
            # Method 2: GET with query parameters (TraceQL format)
            if not result or (isinstance(result, dict) and not result.get("traces") and not result.get("data")):
                logger.debug("POST request failed, trying GET with TraceQL...")
                params_get = {
                    "q": traceql_query,
                    "start": str(start_ns),
                    "end": str(end_ns),
                    "limit": "1000"
                }
                result = self._make_request("/api/search", params_get, method="GET")
            
            # Method 3: Try alternative endpoint /api/traces/search
            if not result or (isinstance(result, dict) and not result.get("traces") and not result.get("data")):
                logger.debug("Standard search failed, trying /api/traces/search...")
                params_alt = {
                    "q": traceql_query,
                    "start": str(start_ns),
                    "end": str(end_ns),
                    "limit": "1000"
                }
                result = self._make_request("/api/traces/search", params_alt, method="GET")
            
            # Method 4: Try without TraceQL (older API format)
            if not result or (isinstance(result, dict) and not result.get("traces") and not result.get("data")):
                logger.debug("TraceQL query failed, trying alternative API format...")
                params_alt = {
                    "start": str(start_ns),
                    "end": str(end_ns),
                    "limit": "1000"
                }
                if service_name:
                    params_alt["tags"] = f"service.name={service_name}"
                
                result = self._make_request("/api/search", params_alt, method="GET")
            
            traces = []
            if isinstance(result, dict):
                # Handle different response formats
                trace_list = []
                if "traces" in result:
                    trace_list = result.get("traces", [])
                elif "data" in result:
                    data = result.get("data", {})
                    if isinstance(data, dict):
                        trace_list = data.get("traces", [])
                    elif isinstance(data, list):
                        trace_list = data
                elif isinstance(result, list):
                    trace_list = result
                
                for trace in trace_list:
                    # Extract duration and error status with multiple field name variations
                    duration_ns = (
                        trace.get("durationNanos") or 
                        trace.get("duration_nanos") or 
                        trace.get("duration") or 
                        0
                    )
                    duration_sec = float(duration_ns) / 1e9 if duration_ns else 0
                    
                    # Check for error status - look for status field or error tags
                    has_error = (
                        trace.get("hasError", False) or 
                        trace.get("has_error", False) or
                        str(trace.get("status", "")).upper() == "ERROR" or
                        trace.get("statusCode", 0) >= 400 or
                        "error" in str(trace.get("tags", {})).lower()
                    )
                    
                    traces.append({
                        "trace_id": trace.get("traceID") or trace.get("trace_id") or trace.get("id", ""),
                        "duration": duration_sec,
                        "has_error": has_error,
                        "service_name": trace.get("rootServiceName") or trace.get("service_name") or trace.get("serviceName", ""),
                        "span_count": trace.get("spanCount") or trace.get("span_count") or trace.get("spanCount", 0)
                    })
            
            if traces:
                logger.info(f"Successfully queried {len(traces)} traces from Tempo")
            else:
                logger.debug(f"No traces found in Tempo (this is normal if no traces exist)")
            
            return traces
        except Exception as e:
            logger.warning(f"Failed to query traces (non-critical, continuing): {e}")
            return []
    
    def get_trace_metrics(
        self,
        start: float,
        end: float,
        step: int = 60
    ) -> Dict[str, np.ndarray]:
        """
        Get trace metrics aggregated by time buckets.
        
        Returns:
            Dictionary with 'durations' and 'error_rates' arrays
        """
        traces = self.query_traces(start, end)
        
        if not traces:
            return {"durations": np.array([]), "error_rates": np.array([])}
        
        # Aggregate by time buckets (simplified: use average duration and error rate)
        # Since we don't have exact timestamps from Tempo search API,
        # we'll aggregate all traces and create time series based on their distribution
        num_buckets = max(1, int((end - start) / step))
        duration_buckets = [[] for _ in range(num_buckets)]
        error_buckets = [0] * num_buckets
        total_buckets = [0] * num_buckets
        
        # Distribute traces evenly across buckets (simplified approach)
        for i, trace in enumerate(traces):
            bucket_idx = i % num_buckets
            duration_buckets[bucket_idx].append(trace.get("duration", 0))
            total_buckets[bucket_idx] += 1
            if trace.get("has_error", False):
                error_buckets[bucket_idx] += 1
        
        # Calculate average durations and error rates
        avg_durations = np.array([
            np.mean(bucket) if bucket else 0.0
            for bucket in duration_buckets
        ])
        
        error_rates = np.array([
            (errors / total) if total > 0 else 0.0
            for errors, total in zip(error_buckets, total_buckets)
        ])
        
        return {
            "durations": avg_durations,
            "error_rates": error_rates
        }


class LokiClient:
    """Client for interacting with Loki API for log anomaly detection."""
    
    def __init__(self, config: AnomalyDetectorConfig):
        """Initialize the Loki client."""
        self.config = config
        self.base_url = config.loki.url.rstrip("/")
        
    def _make_request(self, endpoint: str, params: dict) -> dict:
        """Make a request to Loki API."""
        url = f"{self.base_url}{endpoint}"
        try:
            response = requests.get(url, params=params, timeout=30)
            response.raise_for_status()
            return response.json()
        except requests.exceptions.RequestException as e:
            logger.warning(f"Loki request failed (non-critical): {e}")
            # Don't raise - return empty dict to allow graceful degradation
            return {}
    
    def query_logs(
        self,
        start: float,
        end: float,
        query: str = '{job=~".+"}'
    ) -> List[Dict]:
        """
        Query logs from Loki for a time range.
        
        Args:
            start: Start timestamp (Unix, seconds)
            end: End timestamp (Unix, seconds)
            query: LogQL query string
            
        Returns:
            List of log entries with timestamps and content
        """
        try:
            # Convert to nanoseconds for Loki API (Loki uses nanoseconds as string)
            start_ns = str(int(start * 1e9))
            end_ns = str(int(end * 1e9))
            
            params = {
                "query": query,
                "start": start_ns,
                "end": end_ns,
                "limit": "1000"
            }
            
            result = self._make_request("/loki/api/v1/query_range", params)
            
            logs = []
            if isinstance(result, dict):
                # Handle different response formats
                data = result.get("data", {})
                if isinstance(data, dict):
                    streams = data.get("result", [])
                elif isinstance(data, list):
                    streams = data
                else:
                    streams = []
                
                for stream in streams:
                    # Handle different stream formats
                    if isinstance(stream, dict):
                        values = stream.get("values", [])
                        stream_labels = stream.get("stream", {})
                    elif isinstance(stream, list) and len(stream) >= 2:
                        # Format: [labels, values]
                        stream_labels = stream[0] if isinstance(stream[0], dict) else {}
                        values = stream[1] if isinstance(stream[1], list) else []
                    else:
                        continue
                    
                    for entry in values:
                        if isinstance(entry, list) and len(entry) >= 2:
                            timestamp_ns, log_line = entry[0], entry[1]
                            try:
                                timestamp = float(timestamp_ns) / 1e9
                                logs.append({
                                    "timestamp": timestamp,
                                    "content": str(log_line),
                                    "stream": stream_labels
                                })
                            except (ValueError, TypeError):
                                continue
            
            if logs:
                logger.info(f"Successfully queried {len(logs)} log entries from Loki")
            else:
                logger.debug(f"No logs found in Loki (this is normal if no logs exist)")
            
            return logs
        except Exception as e:
            logger.warning(f"Failed to query logs (non-critical, continuing): {e}")
            return []
    
    def get_log_metrics(
        self,
        start: float,
        end: float,
        step: int = 60
    ) -> Dict[str, np.ndarray]:
        """
        Get log metrics aggregated by time buckets.
        
        Returns:
            Dictionary with 'error_counts' and 'log_volumes' arrays
        """
        # Query for error logs
        error_logs = self.query_logs(start, end, query='{job=~".+"} |= "error"')
        all_logs = self.query_logs(start, end, query='{job=~".+"}')
        
        if not all_logs:
            return {"error_counts": np.array([]), "log_volumes": np.array([])}
        
        # Aggregate by time buckets
        num_buckets = int((end - start) / step)
        error_buckets = [0] * num_buckets
        volume_buckets = [0] * num_buckets
        
        # Create error log timestamps set for faster lookup
        error_timestamps = {
            int((log["timestamp"] - start) / step)
            for log in error_logs
        }
        
        for log in all_logs:
            bucket_idx = int((log["timestamp"] - start) / step)
            if 0 <= bucket_idx < num_buckets:
                volume_buckets[bucket_idx] += 1
                if bucket_idx in error_timestamps:
                    error_buckets[bucket_idx] += 1
        
        return {
            "error_counts": np.array(error_buckets),
            "log_volumes": np.array(volume_buckets)
        }


class AnomalyDetectorService:
    """
    Service for automatic anomaly detection in:
    - Prometheus metrics (primary)
    - Tempo traces (optional)
    - Loki logs (optional)
    
    This service:
    1. Discovers data from all enabled sources
    2. Periodically fetches data
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
        
        # Initialize Tempo and Loki clients if enabled
        self.tempo_client = None
        if self.config.tempo.enabled:
            try:
                self.tempo_client = TempoClient(self.config)
                logger.info("Tempo client initialized")
            except Exception as e:
                logger.warning(f"Failed to initialize Tempo client: {e}")
        
        self.loki_client = None
        if self.config.loki.enabled:
            try:
                self.loki_client = LokiClient(self.config)
                logger.info("Loki client initialized")
            except Exception as e:
                logger.warning(f"Failed to initialize Loki client: {e}")
        
        # Get the appropriate detector
        self.detector = get_detector(
            algorithm=self.config.algorithm,
            contamination=self.config.contamination_rate,
            zscore_threshold=self.config.zscore_threshold
        )
        
        # Metric history for tracking data over time
        self.metric_history: Dict[str, List[float]] = defaultdict(list)
        
        # Trace and log history
        self.trace_history: Dict[str, List[float]] = defaultdict(list)
        self.log_history: Dict[str, List[float]] = defaultdict(list)
        
        # Set of active anomaly fingerprints (to avoid duplicate alerts)
        self.active_anomalies: Set[str] = set()
        
        # Alert history for export (stores all alerts with data source type)
        self.alert_history: List[Dict] = []
        
        # Control flags
        self._stop = False
        self._thread: Optional[threading.Thread] = None
        
        logger.info(
            f"Anomaly Detector Service initialized with algorithm: {self.config.algorithm}, "
            f"detection interval: {self.config.detection_interval}s, "
            f"metrics: enabled, traces: {self.config.tempo.enabled}, logs: {self.config.loki.enabled}"
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
                # Detect anomalies in all enabled data sources
                self._detect_anomalies()  # Metrics
                if self.tempo_client:
                    self._detect_trace_anomalies()  # Traces
                if self.loki_client:
                    self._detect_log_anomalies()  # Logs
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
        if is_counter:
            # Use rate() with a window of at least 2m (120s) for counter metrics
            # 15s window is too short and returns no data - need at least 2-3 minutes
            # This converts cumulative values to per-second rates
            rate_window = "2m"  # Use 2 minutes window for rate() calculation
            query = f"rate({metric_name}[{rate_window}])"
            logger.debug(f"Using rate() for counter metric: {metric_name} with window {rate_window}")
        else:
            # For gauge-type metrics, use raw values
            query = metric_name
            logger.debug(f"Using raw value for gauge metric: {metric_name}")
        
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
                
                # Use 70% of stable history for baseline, detect on last 30%
                baseline_size = max(
                    self.config.min_data_points,
                    int(len(stable_history) * 0.7)
                )
                # But always reserve at least min_detection_points for detection
                baseline_size = min(baseline_size, len(stable_history) - min_detection_points)
                
                if len(stable_history) > baseline_size:
                    baseline = stable_history[:baseline_size]
                    detection_window = stable_history[baseline_size:]
                    
                    # Use median for baseline to be more robust against outliers
                    # This prevents baseline from being contaminated by early anomalies
                    baseline_median = np.median(baseline)
                    baseline_mean = np.mean(baseline)
                    
                    # Use the larger of median or mean to avoid false positives
                    # But prefer median as it's more robust
                    baseline_value = max(baseline_median, baseline_mean * 0.8)
                    
                    # If baseline is zero, we cannot compute a meaningful increase ratio
                    if baseline_value == 0:
                        logger.debug(f"Skipping {metric_name}: baseline value is zero")
                        continue
                    
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
                                f"(baseline: {len(baseline)} points from stable history, detection window: {len(detection_window)} points, "
                                f"startup exclusion: {startup_exclusion_size} points, "
                                f"rate_change_threshold: {self.config.rate_change_threshold}, total anomalies in window: {len(anomalies_in_window)})"
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
    
    def _detect_trace_anomalies(self):
        """Run anomaly detection on trace data from Tempo or Prometheus."""
        from keep.anomaly_detector.algorithms import AnomalyResult
        
        try:
            end_time = time.time()
            start_time = end_time - self.config.query_time_range
            
            logger.debug("Starting trace anomaly detection...")
            
            # Try to get trace metrics from Tempo first
            trace_metrics = None
            if self.tempo_client:
                try:
                    trace_metrics = self.tempo_client.get_trace_metrics(
                        start=start_time,
                        end=end_time,
                        step=int(self.config.query_time_range / 60)
                    )
                except Exception as e:
                    logger.debug(f"Tempo query failed, trying Prometheus fallback: {e}")
            
            # Fallback: Try to get trace-related metrics from Prometheus
            if not trace_metrics or (len(trace_metrics.get("durations", [])) == 0 and len(trace_metrics.get("error_rates", [])) == 0):
                logger.debug("Trying to get trace metrics from Prometheus as fallback...")
                try:
                    # Use actual trace metrics that exist in Prometheus
                    # Based on metrics from Tempo's metrics generator
                    trace_metric_queries = [
                        # Trace latency (p95)
                        "histogram_quantile(0.95, sum(rate(traces_spanmetrics_latency_bucket[5m])) by (le))",
                        # Trace call rate
                        "sum(rate(traces_spanmetrics_calls_total[5m]))",
                        # Failed trace requests
                        "sum(rate(traces_service_graph_request_failed_total[5m]))",
                        # Trace creation rate
                        "sum(rate(tempo_ingester_traces_created_total[5m]))",
                        # Trace size
                        "sum(rate(traces_spanmetrics_size_total[5m]))",
                    ]
                    
                    # Try to find any trace-related metrics
                    for query in trace_metric_queries:
                        try:
                            results = self.prometheus_client.query_range(
                                query=query,
                                start=start_time,
                                end=end_time,
                                step=self.config.query_step
                            )
                            if results:
                                # Convert to numpy array
                                values = []
                                for series in results:
                                    for _, value in series.get("values", []):
                                        try:
                                            values.append(float(value))
                                        except (ValueError, TypeError):
                                            continue
                                
                                if len(values) >= self.config.min_data_points:
                                    values_array = np.array(values)
                                    logger.info(f"Found trace metrics in Prometheus: {query}")
                                    self._detect_anomalies_in_series(
                                        source_type="traces",
                                        identifier=f"trace_metric_{query[:30]}",
                                        values=values_array,
                                        metric_name=query
                                    )
                                    break  # Found one, that's enough
                        except Exception as e:
                            logger.debug(f"Prometheus trace query failed: {query}, {e}")
                            continue
                except Exception as e:
                    logger.debug(f"Prometheus fallback failed: {e}")
            
            # If we got trace metrics from Tempo, use them
            if trace_metrics:
                # Detect anomalies in trace durations
                durations = trace_metrics.get("durations", np.array([]))
                if len(durations) > 0 and len(durations) >= self.config.min_data_points:
                    logger.debug(f"Detecting anomalies in trace durations: {len(durations)} data points")
                    self._detect_anomalies_in_series(
                        source_type="traces",
                        identifier="trace_duration",
                        values=durations,
                        metric_name="trace_duration"
                    )
                elif len(durations) > 0:
                    logger.debug(f"Not enough trace duration data points: {len(durations)} < {self.config.min_data_points}")
                
                # Detect anomalies in error rates
                error_rates = trace_metrics.get("error_rates", np.array([]))
                if len(error_rates) > 0 and len(error_rates) >= self.config.min_data_points:
                    logger.debug(f"Detecting anomalies in trace error rates: {len(error_rates)} data points")
                    self._detect_anomalies_in_series(
                        source_type="traces",
                        identifier="trace_error_rate",
                        values=error_rates,
                        metric_name="trace_error_rate"
                    )
                elif len(error_rates) > 0:
                    logger.debug(f"Not enough trace error rate data points: {len(error_rates)} < {self.config.min_data_points}")
                else:
                    logger.debug("No trace data available for anomaly detection")
        except Exception as e:
            logger.warning(f"Error detecting trace anomalies (non-critical, continuing): {e}")
            # Don't raise - allow metrics and logs detection to continue
    
    def _detect_log_anomalies(self):
        """Run anomaly detection on log data from Loki or Prometheus."""
        from keep.anomaly_detector.algorithms import AnomalyResult
        
        try:
            end_time = time.time()
            start_time = end_time - self.config.query_time_range
            
            logger.debug("Starting log anomaly detection...")
            
            # Try to get log metrics from Loki first
            log_metrics = None
            if self.loki_client:
                try:
                    log_metrics = self.loki_client.get_log_metrics(
                        start=start_time,
                        end=end_time,
                        step=int(self.config.query_time_range / 60)
                    )
                except Exception as e:
                    logger.debug(f"Loki query failed, trying Prometheus fallback: {e}")
            
            # Fallback: Try to get log-related metrics from Prometheus
            if not log_metrics or (len(log_metrics.get("error_counts", [])) == 0 and len(log_metrics.get("log_volumes", [])) == 0):
                logger.debug("Trying to get log metrics from Prometheus as fallback...")
                try:
                    # Common log-related metrics in Prometheus
                    log_metric_queries = [
                        "sum(rate(log_entries_total[5m]))",
                        "sum(rate(log_errors_total[5m]))",
                        "sum(rate(loki_distributor_lines_per_second[5m]))",
                    ]
                    
                    # Try to find any log-related metrics
                    for query in log_metric_queries:
                        try:
                            results = self.prometheus_client.query_range(
                                query=query,
                                start=start_time,
                                end=end_time,
                                step=self.config.query_step
                            )
                            if results:
                                # Convert to numpy array
                                values = []
                                for series in results:
                                    for _, value in series.get("values", []):
                                        try:
                                            values.append(float(value))
                                        except (ValueError, TypeError):
                                            continue
                                
                                if len(values) >= self.config.min_data_points:
                                    values_array = np.array(values)
                                    logger.info(f"Found log metrics in Prometheus: {query}")
                                    self._detect_anomalies_in_series(
                                        source_type="logs",
                                        identifier=f"log_metric_{query[:20]}",
                                        values=values_array,
                                        metric_name=query
                                    )
                                    break  # Found one, that's enough
                        except Exception as e:
                            logger.debug(f"Prometheus log query failed: {query}, {e}")
                            continue
                except Exception as e:
                    logger.debug(f"Prometheus fallback failed: {e}")
            
            # If we got log metrics from Loki, use them
            if log_metrics:
                # Detect anomalies in error log counts
                error_counts = log_metrics.get("error_counts", np.array([]))
                if len(error_counts) > 0 and len(error_counts) >= self.config.min_data_points:
                    logger.debug(f"Detecting anomalies in log error counts: {len(error_counts)} data points")
                    self._detect_anomalies_in_series(
                        source_type="logs",
                        identifier="log_error_count",
                        values=error_counts,
                        metric_name="log_error_count"
                    )
                elif len(error_counts) > 0:
                    logger.debug(f"Not enough log error count data points: {len(error_counts)} < {self.config.min_data_points}")
                
                # Detect anomalies in log volumes
                log_volumes = log_metrics.get("log_volumes", np.array([]))
                if len(log_volumes) > 0 and len(log_volumes) >= self.config.min_data_points:
                    logger.debug(f"Detecting anomalies in log volumes: {len(log_volumes)} data points")
                    self._detect_anomalies_in_series(
                        source_type="logs",
                        identifier="log_volume",
                        values=log_volumes,
                        metric_name="log_volume"
                    )
                elif len(log_volumes) > 0:
                    logger.debug(f"Not enough log volume data points: {len(log_volumes)} < {self.config.min_data_points}")
                else:
                    logger.debug("No log data available for anomaly detection")
        except Exception as e:
            logger.warning(f"Error detecting log anomalies (non-critical, continuing): {e}")
            # Don't raise - allow metrics detection to continue
    
    def _detect_anomalies_in_series(
        self,
        source_type: str,
        identifier: str,
        values: np.ndarray,
        metric_name: str
    ):
        """
        Generic method to detect anomalies in a time series.
        
        Args:
            source_type: Type of data source ("metrics", "traces", "logs")
            identifier: Identifier for the data
            values: Time series values
            metric_name: Name for logging
        """
        from keep.anomaly_detector.algorithms import AnomalyResult
        
        if len(values) < self.config.min_data_points:
            return
        
        history = values
        
        # Exclude startup period
        startup_exclusion_ratio = 0.3
        startup_exclusion_size = max(1, int(len(history) * startup_exclusion_ratio))
        stable_history = history[startup_exclusion_size:]
        
        min_detection_points = 10
        if len(stable_history) < self.config.min_data_points + min_detection_points:
            return
        
        # Use 70% of stable history for baseline, detect on last 30%
        baseline_size = max(
            self.config.min_data_points,
            int(len(stable_history) * 0.7)
        )
        baseline_size = min(baseline_size, len(stable_history) - min_detection_points)
        
        if len(stable_history) > baseline_size:
            baseline = stable_history[:baseline_size]
            detection_window = stable_history[baseline_size:]
            
            baseline_median = np.median(baseline)
            baseline_mean = np.mean(baseline)
            baseline_value = max(baseline_median, baseline_mean * 0.8)
            
            if baseline_value == 0:
                return
            
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
                recent_window_size = min(5, len(detection_window))
                recent_anomalies_list = [
                    a for a in anomalies_in_window 
                    if a['index'] >= len(stable_history) - recent_window_size
                ]
                
                if len(recent_anomalies_list) >= 1:
                    logger.info(
                        f"Detected {len(recent_anomalies_list)} recent anomalies in {source_type}:{identifier}"
                    )
                    
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
                    
                    result = DetectionResult(
                        metric_name=identifier,
                        anomalies=anomaly_results,
                        total_points=len(history),
                        anomaly_count=len(recent_anomalies_list),
                        mean=float(baseline_mean),
                        std=float(np.std(baseline)),
                        algorithm="rate_change"
                    )
                    self._create_alert(source_type, identifier, result, anomaly_results)
    
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
            source_type: Type of data source ("metrics", "traces", "logs")
            identifier: Identifier for the data (metric name, service name, etc.)
            result: Detection result
            recent_anomalies: List of recent anomalies
        """
        logger.info(f"Creating alert for {source_type}:{identifier} with {len(recent_anomalies)} anomalies")
        
        fingerprint = self._generate_fingerprint(source_type, identifier)
        
        # Check if we already have an active alert for this
        if fingerprint in self.active_anomalies:
            logger.info(f"Alert already active for {source_type}:{identifier}, skipping")
            return
        
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

