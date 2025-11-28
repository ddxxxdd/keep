"""
Anomaly Detection Algorithms for Keep Platform.

This module provides various algorithms for detecting anomalies in time series data:
- Isolation Forest: Unsupervised machine learning algorithm for anomaly detection
- Z-Score: Statistical method based on standard deviation
"""
import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import List, Optional, Tuple

import numpy as np

logger = logging.getLogger(__name__)


@dataclass
class AnomalyResult:
    """Result of anomaly detection for a single data point."""
    index: int
    value: float
    is_anomaly: bool
    score: float
    method: str
    details: dict


@dataclass
class DetectionResult:
    """Result of anomaly detection for a time series."""
    metric_name: str
    anomalies: List[AnomalyResult]
    total_points: int
    anomaly_count: int
    mean: float
    std: float
    algorithm: str


class BaseAnomalyDetector(ABC):
    """Base class for anomaly detectors."""
    
    @abstractmethod
    def detect(self, values: np.ndarray, metric_name: str = "") -> DetectionResult:
        """Detect anomalies in the given values."""
        pass
    
    @abstractmethod
    def get_name(self) -> str:
        """Get the name of the algorithm."""
        pass


class IsolationForestDetector(BaseAnomalyDetector):
    """
    Isolation Forest based anomaly detection.
    
    Isolation Forest is an unsupervised learning algorithm that isolates anomalies
    by randomly selecting a feature and then randomly selecting a split value.
    Anomalies are easier to isolate, so they have shorter path lengths.
    """
    
    def __init__(self, contamination: float = 0.05, n_estimators: int = 100):
        """
        Initialize the Isolation Forest detector.
        
        Args:
            contamination: Expected proportion of anomalies in the data (0.0 to 0.5)
            n_estimators: Number of trees in the forest
        """
        self.contamination = contamination
        self.n_estimators = n_estimators
        
    def get_name(self) -> str:
        return "isolation_forest"
    
    def detect(self, values: np.ndarray, metric_name: str = "") -> DetectionResult:
        """
        Detect anomalies using Isolation Forest.
        
        Args:
            values: Numpy array of values to analyze
            metric_name: Name of the metric being analyzed
            
        Returns:
            DetectionResult with detected anomalies
        """
        if len(values) < 10:
            logger.warning(f"Not enough data points for Isolation Forest: {len(values)}")
            return DetectionResult(
                metric_name=metric_name,
                anomalies=[],
                total_points=len(values),
                anomaly_count=0,
                mean=float(np.mean(values)) if len(values) > 0 else 0.0,
                std=float(np.std(values)) if len(values) > 0 else 0.0,
                algorithm=self.get_name()
            )
        
        try:
            from sklearn.ensemble import IsolationForest
        except ImportError:
            logger.error("scikit-learn is not installed. Please install it with: pip install scikit-learn")
            raise ImportError("scikit-learn is required for Isolation Forest detection")
        
        # Reshape for sklearn
        X = values.reshape(-1, 1)
        
        # Create and fit the model
        clf = IsolationForest(
            contamination=self.contamination,
            n_estimators=self.n_estimators,
            random_state=42
        )
        
        # Fit and predict
        predictions = clf.fit_predict(X)
        scores = clf.decision_function(X)
        
        # Collect anomalies
        anomalies = []
        for i, (pred, score, value) in enumerate(zip(predictions, scores, values)):
            if pred == -1:  # Anomaly
                anomalies.append(AnomalyResult(
                    index=i,
                    value=float(value),
                    is_anomaly=True,
                    score=float(score),
                    method=self.get_name(),
                    details={
                        "contamination": self.contamination,
                        "n_estimators": self.n_estimators
                    }
                ))
        
        return DetectionResult(
            metric_name=metric_name,
            anomalies=anomalies,
            total_points=len(values),
            anomaly_count=len(anomalies),
            mean=float(np.mean(values)),
            std=float(np.std(values)),
            algorithm=self.get_name()
        )


class ZScoreDetector(BaseAnomalyDetector):
    """
    Z-Score (Standard Deviation) based anomaly detection.
    
    This method detects anomalies by calculating how many standard deviations
    each data point is from the mean. Points beyond the threshold are considered anomalies.
    """
    
    def __init__(self, threshold: float = 3.0, window_size: Optional[int] = None):
        """
        Initialize the Z-Score detector.
        
        Args:
            threshold: Z-score threshold for anomaly detection (default 3.0 for 3σ)
            window_size: Optional sliding window size for local anomaly detection
        """
        self.threshold = threshold
        self.window_size = window_size
        
    def get_name(self) -> str:
        return "zscore"
    
    def detect(self, values: np.ndarray, metric_name: str = "") -> DetectionResult:
        """
        Detect anomalies using Z-Score method.
        
        Args:
            values: Numpy array of values to analyze
            metric_name: Name of the metric being analyzed
            
        Returns:
            DetectionResult with detected anomalies
        """
        if len(values) < 3:
            logger.warning(f"Not enough data points for Z-Score: {len(values)}")
            return DetectionResult(
                metric_name=metric_name,
                anomalies=[],
                total_points=len(values),
                anomaly_count=0,
                mean=float(np.mean(values)) if len(values) > 0 else 0.0,
                std=float(np.std(values)) if len(values) > 0 else 0.0,
                algorithm=self.get_name()
            )
        
        anomalies = []
        
        if self.window_size and self.window_size < len(values):
            # Sliding window approach
            for i in range(self.window_size, len(values)):
                window = values[i - self.window_size:i]
                mean = np.mean(window)
                std = np.std(window)
                
                if std == 0:
                    continue
                    
                current_value = values[i]
                z_score = abs(current_value - mean) / std
                
                if z_score > self.threshold:
                    anomalies.append(AnomalyResult(
                        index=i,
                        value=float(current_value),
                        is_anomaly=True,
                        score=float(z_score),
                        method=self.get_name(),
                        details={
                            "threshold": self.threshold,
                            "window_mean": float(mean),
                            "window_std": float(std),
                            "window_size": self.window_size
                        }
                    ))
        else:
            # Global approach
            mean = np.mean(values)
            std = np.std(values)
            
            if std == 0:
                return DetectionResult(
                    metric_name=metric_name,
                    anomalies=[],
                    total_points=len(values),
                    anomaly_count=0,
                    mean=float(mean),
                    std=0.0,
                    algorithm=self.get_name()
                )
            
            for i, value in enumerate(values):
                z_score = abs(value - mean) / std
                if z_score > self.threshold:
                    anomalies.append(AnomalyResult(
                        index=i,
                        value=float(value),
                        is_anomaly=True,
                        score=float(z_score),
                        method=self.get_name(),
                        details={
                            "threshold": self.threshold,
                            "global_mean": float(mean),
                            "global_std": float(std)
                        }
                    ))
        
        return DetectionResult(
            metric_name=metric_name,
            anomalies=anomalies,
            total_points=len(values),
            anomaly_count=len(anomalies),
            mean=float(np.mean(values)),
            std=float(np.std(values)),
            algorithm=self.get_name()
        )


class CombinedDetector(BaseAnomalyDetector):
    """
    Combined anomaly detector that uses multiple algorithms.
    
    An anomaly is reported if it's detected by any of the configured algorithms.
    """
    
    def __init__(
        self,
        contamination: float = 0.05,
        zscore_threshold: float = 3.0,
        use_isolation_forest: bool = True,
        use_zscore: bool = True
    ):
        """
        Initialize the combined detector.
        
        Args:
            contamination: Contamination rate for Isolation Forest
            zscore_threshold: Threshold for Z-Score detection
            use_isolation_forest: Whether to use Isolation Forest
            use_zscore: Whether to use Z-Score
        """
        self.detectors: List[BaseAnomalyDetector] = []
        
        if use_isolation_forest:
            self.detectors.append(IsolationForestDetector(contamination=contamination))
        if use_zscore:
            self.detectors.append(ZScoreDetector(threshold=zscore_threshold))
            
    def get_name(self) -> str:
        return "combined"
    
    def detect(self, values: np.ndarray, metric_name: str = "") -> DetectionResult:
        """
        Detect anomalies using multiple algorithms.
        
        An anomaly is reported if detected by at least one algorithm.
        
        Args:
            values: Numpy array of values to analyze
            metric_name: Name of the metric being analyzed
            
        Returns:
            DetectionResult with detected anomalies
        """
        all_anomaly_indices = set()
        all_anomalies_by_index = {}
        
        for detector in self.detectors:
            result = detector.detect(values, metric_name)
            for anomaly in result.anomalies:
                if anomaly.index not in all_anomaly_indices:
                    all_anomaly_indices.add(anomaly.index)
                    all_anomalies_by_index[anomaly.index] = anomaly
                else:
                    # Merge detection methods
                    existing = all_anomalies_by_index[anomaly.index]
                    existing.method = f"{existing.method},{anomaly.method}"
                    existing.details.update(anomaly.details)
        
        # Sort by index
        sorted_anomalies = [
            all_anomalies_by_index[idx] 
            for idx in sorted(all_anomaly_indices)
        ]
        
        return DetectionResult(
            metric_name=metric_name,
            anomalies=sorted_anomalies,
            total_points=len(values),
            anomaly_count=len(sorted_anomalies),
            mean=float(np.mean(values)) if len(values) > 0 else 0.0,
            std=float(np.std(values)) if len(values) > 0 else 0.0,
            algorithm=self.get_name()
        )


def get_detector(
    algorithm: str = "both",
    contamination: float = 0.05,
    zscore_threshold: float = 3.0
) -> BaseAnomalyDetector:
    """
    Factory function to get the appropriate anomaly detector.
    
    Args:
        algorithm: Algorithm to use ("isolation_forest", "zscore", or "both")
        contamination: Contamination rate for Isolation Forest
        zscore_threshold: Threshold for Z-Score detection
        
    Returns:
        An instance of the appropriate detector
    """
    if algorithm == "isolation_forest":
        return IsolationForestDetector(contamination=contamination)
    elif algorithm == "zscore":
        return ZScoreDetector(threshold=zscore_threshold)
    else:  # "both" or any other value
        return CombinedDetector(
            contamination=contamination,
            zscore_threshold=zscore_threshold
        )

