import os
import time
import logging
import requests

from keep.api.core.demo_mode import launch_demo_mode_thread
from keep.api.core.report_uptime import launch_uptime_reporting_thread
from keep.anomaly_detector.anomaly_detector_service import launch_anomaly_detector_thread

logger = logging.getLogger(__name__)


def main():
    logger.info("Starting background server jobs.")

    # Use KEEP_API_URL if set (for containerized environments), otherwise use localhost
    keep_api_url = os.environ.get("KEEP_API_URL")
    if not keep_api_url:
        # We intentionally don't use KEEP_API_URL here to avoid going through the internet.
        # Script should be launched in the same environment as the server.
        keep_api_url = "http://localhost:" + str(os.environ.get("PORT", 8080))
    keep_api_key = os.environ.get("KEEP_LIVE_DEMO_MODE_API_KEY")

    while True:
        try:
            logger.info(f"Checking if server is up at {keep_api_url}...")
            response = requests.get(keep_api_url)
            response.raise_for_status()
            break
        except requests.exceptions.RequestException:
            logger.info("API is not up yet. Waiting...")
            time.sleep(5)

    threads = []
    threads.append(launch_demo_mode_thread(keep_api_url, keep_api_key))
    threads.append(launch_uptime_reporting_thread())
    
    # Launch the Anomaly Detector Service
    # This service automatically detects anomalies in Prometheus metrics
    # Configure with environment variables: ANOMALY_DETECTOR_ENABLED, PROMETHEUS_URL, etc.
    logger.info("Launching Anomaly Detector Service...")
    threads.append(launch_anomaly_detector_thread())

    logger.info("Background server jobs threads launched, joining them.")
    
    for thread in threads:
        if thread is not None:
            thread.join()

    logger.info("Background server jobs script executed and exiting.")


if __name__ == "__main__":
    """
    This script should be executed alongside to the server.
    Running it in the same process as the server may (and most probably will) cause issues.
    """
    main()
