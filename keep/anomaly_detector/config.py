"""
Configuration for the Anomaly Detector Service.
"""
import os
from dataclasses import dataclass, field
from typing import List, Optional


@dataclass
class PrometheusConfig:
    """Configuration for Prometheus connection."""
    url: str = field(default_factory=lambda: os.environ.get("PROMETHEUS_URL", "http://localhost:9090"))
    username: str = field(default_factory=lambda: os.environ.get("PROMETHEUS_USERNAME", ""))
    password: str = field(default_factory=lambda: os.environ.get("PROMETHEUS_PASSWORD", ""))
    verify_ssl: bool = field(default_factory=lambda: os.environ.get("PROMETHEUS_VERIFY_SSL", "true").lower() == "true")


@dataclass
class TempoConfig:
    """Configuration for Tempo (traces) connection."""
    url: str = field(default_factory=lambda: os.environ.get("TEMPO_URL", "http://tempo:3200"))
    enabled: bool = field(default_factory=lambda: os.environ.get("ANOMALY_DETECTOR_TRACES_ENABLED", "false").lower() == "true")


@dataclass
class LokiConfig:
    """Configuration for Loki (logs) connection."""
    url: str = field(default_factory=lambda: os.environ.get("LOKI_URL", "http://loki:3100"))
    enabled: bool = field(default_factory=lambda: os.environ.get("ANOMALY_DETECTOR_LOGS_ENABLED", "false").lower() == "true")


@dataclass
class AnomalyDetectorConfig:
    """Configuration for the Anomaly Detector Service."""
    
    # Detection interval in seconds
    detection_interval: int = field(
        default_factory=lambda: int(os.environ.get("ANOMALY_DETECTOR_INTERVAL", "300"))
    )
    
    # Minimum data points required for detection
    min_data_points: int = field(
        default_factory=lambda: int(os.environ.get("ANOMALY_DETECTOR_MIN_DATA_POINTS", "20"))
    )
    
    # Maximum number of metrics to monitor (0 = unlimited)
    max_metrics: int = field(
        default_factory=lambda: int(os.environ.get("ANOMALY_DETECTOR_MAX_METRICS", "100"))
    )
    
    # Isolation Forest contamination rate (expected proportion of anomalies)
    contamination_rate: float = field(
        default_factory=lambda: float(os.environ.get("ANOMALY_DETECTOR_CONTAMINATION", "0.05"))
    )
    
    # Z-Score threshold for anomaly detection
    zscore_threshold: float = field(
        default_factory=lambda: float(os.environ.get("ANOMALY_DETECTOR_ZSCORE_THRESHOLD", "3.0"))
    )
    
    # History size (number of data points to keep)
    history_size: int = field(
        default_factory=lambda: int(os.environ.get("ANOMALY_DETECTOR_HISTORY_SIZE", "1000"))
    )
    
    # Time range for Prometheus queries (in seconds, e.g., 3600 = last 1 hour)
    query_time_range: int = field(
        default_factory=lambda: int(os.environ.get("ANOMALY_DETECTOR_QUERY_RANGE", "3600"))
    )
    
    # Query step interval (in seconds)
    query_step: str = field(
        default_factory=lambda: os.environ.get("ANOMALY_DETECTOR_QUERY_STEP", "60s")
    )
    
    # Metrics to include (regex patterns, empty = all)
    include_metrics: List[str] = field(
        default_factory=lambda: os.environ.get("ANOMALY_DETECTOR_INCLUDE_METRICS", "").split(",") if os.environ.get("ANOMALY_DETECTOR_INCLUDE_METRICS") else []
    )
    
    # Metrics to exclude (regex patterns)
    exclude_metrics: List[str] = field(
        default_factory=lambda: os.environ.get("ANOMALY_DETECTOR_EXCLUDE_METRICS", "").split(",") if os.environ.get("ANOMALY_DETECTOR_EXCLUDE_METRICS") else [
            "^go_.*",  # Go runtime metrics
            "^process_.*",  # Process metrics
            "^promhttp_.*",  # Prometheus HTTP metrics
        ]
    )
    
    # Rate change threshold (e.g., 0.5 = +50% vs baseline)
    rate_change_threshold: float = field(
        default_factory=lambda: float(os.environ.get("ANOMALY_DETECTOR_RATE_CHANGE_THRESHOLD", "0.5"))
    )
    
    # Keep API URL for posting alerts
    keep_api_url: str = field(
        default_factory=lambda: os.environ.get("KEEP_API_URL", "http://localhost:8080")
    )
    
    # Keep API key for authentication
    keep_api_key: str = field(
        default_factory=lambda: os.environ.get("KEEP_API_KEY", "")
    )
    
    # Tenant ID
    tenant_id: str = field(
        default_factory=lambda: os.environ.get("KEEP_TENANT_ID", "singletenant")
    )
    
    # Enable/disable the service
    enabled: bool = field(
        default_factory=lambda: os.environ.get("ANOMALY_DETECTOR_ENABLED", "true").lower() == "true"
    )
    
    # Detection algorithm: "isolation_forest", "zscore", or "both"
    algorithm: str = field(
        default_factory=lambda: os.environ.get("ANOMALY_DETECTOR_ALGORITHM", "both")
    )
    
    # Prometheus config
    prometheus: PrometheusConfig = field(default_factory=PrometheusConfig)
    
    # Tempo (traces) config
    tempo: TempoConfig = field(default_factory=TempoConfig)
    
    # Loki (logs) config
    loki: LokiConfig = field(default_factory=LokiConfig)


def get_config() -> AnomalyDetectorConfig:
    """Get the anomaly detector configuration."""
    return AnomalyDetectorConfig()

