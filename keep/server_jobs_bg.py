import os
import time
import logging
import requests

from keep.api.core.demo_mode import launch_demo_mode_thread
from keep.api.core.report_uptime import launch_uptime_reporting_thread

logger = logging.getLogger(__name__)


def main():
    """
    后台任务启动入口。

    目前仅包含：
    - Demo 模式数据上报线程
    - Uptime 上报线程

    异常检测功能已经改为通过自定义 Provider 使用，不再在这里以独立服务形式启动。
    """
    logger.info("Starting background server jobs.")

    # 优先使用 KEEP_API_URL（容器环境），否则回退到本地端口
    keep_api_url = os.environ.get("KEEP_API_URL")
    if not keep_api_url:
        # 不通过公网，而是在同一运行环境内直接访问本地服务
        keep_api_url = "http://localhost:" + str(os.environ.get("PORT", 8080))
    keep_api_key = os.environ.get("KEEP_LIVE_DEMO_MODE_API_KEY")

    # 等待 API 服务可用
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

    logger.info("Background server jobs threads launched, joining them.")

    for thread in threads:
        if thread is not None:
            thread.join()

    logger.info("Background server jobs script executed and exiting.")


if __name__ == "__main__":
    main()
