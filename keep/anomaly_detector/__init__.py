"""
Anomaly Detector Service for Keep Platform

This module provides automatic anomaly detection for metrics from Prometheus.
It uses machine learning algorithms (Isolation Forest, Z-Score) to detect
anomalies without requiring manual threshold configuration.
"""

from keep.anomaly_detector.anomaly_detector_service import AnomalyDetectorService

__all__ = ["AnomalyDetectorService"]

