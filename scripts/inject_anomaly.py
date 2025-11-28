"""
Utility script to generate synthetic traffic spikes so the anomaly detection
service can detect an outlier.

It works in two phases:
1. Warm-up: send steady traffic so the detector learns a normal baseline.
2. Spike: send a burst of requests to create an anomaly.

Usage:
    python scripts/inject_anomaly.py --url http://localhost:8080 --normal 30 --spike 200

All parameters are optional.
"""
from __future__ import annotations

import argparse
import concurrent.futures
import random
import time
from typing import Optional

import requests


def send_request(url: str, timeout: float = 1.0) -> bool:
    try:
        response = requests.get(url, timeout=timeout)
        return response.status_code == 200
    except requests.RequestException:
        return False


def warm_up(url: str, iterations: int, interval: float):
    print(f"Warm-up phase: {iterations} requests (interval {interval}s)")
    for i in range(iterations):
        ok = send_request(url)
        status = "OK" if ok else "FAIL"
        print(f"[warm-up] {i + 1}/{iterations} -> {status}")
        time.sleep(interval + random.uniform(-0.2, 0.2))


def spike(url: str, batch_size: int, concurrency: int, duration: int):
    print(f"\nSpike phase: sustained high traffic for {duration}s (batch={batch_size}, concurrency={concurrency})")
    end_time = time.time() + duration
    total_requests = 0
    total_success = 0
    with concurrent.futures.ThreadPoolExecutor(max_workers=concurrency) as executor:
        while time.time() < end_time:
            futures = [executor.submit(send_request, url, 0.5) for _ in range(batch_size)]
            success = sum(1 for f in concurrent.futures.as_completed(futures) if f.result())
            total_requests += batch_size
            total_success += success
            print(
                f"Spike ongoing... sent {total_requests} requests "
                f"({total_success} successful)",
                end="\r"
            )
            time.sleep(1)
    print(f"\nSpike completed: {total_success}/{total_requests} successful requests")


def main():
    parser = argparse.ArgumentParser(description="Generate anomaly traffic for Keep backend.")
    parser.add_argument("--url", default="http://localhost:8080/healthcheck", help="Endpoint to hit.")
    parser.add_argument("--normal", type=int, default=20, help="Number of warm-up requests.")
    parser.add_argument("--interval", type=float, default=3.0, help="Seconds between warm-up requests.")
    parser.add_argument("--spike", type=int, default=200, help="Requests per spike batch.")
    parser.add_argument("--concurrency", type=int, default=50, help="Concurrent workers during spike.")
    parser.add_argument("--spike-duration", type=int, default=60, help="Spike duration in seconds.")
    parser.add_argument("--spike-rounds", type=int, default=2, help="Number of spike rounds to ensure anomaly is detected.")
    args = parser.parse_args()

    print(f"Target endpoint: {args.url}")
    print("=" * 60)
    warm_up(args.url, args.normal, args.interval)
    
    # Multiple spike rounds to ensure anomaly is prominent
    for round_num in range(1, args.spike_rounds + 1):
        print(f"\n{'=' * 60}")
        print(f"Spike Round {round_num}/{args.spike_rounds}")
        spike(args.url, args.spike, args.concurrency, args.spike_duration)
        if round_num < args.spike_rounds:
            print(f"Waiting 5 seconds before next spike round...")
            time.sleep(5)
    
    print("\n" + "=" * 60)
    print("Traffic injection finished. Wait for the anomaly detector (≈2 min) and check logs:")
    print("  docker-compose -f docker-compose-with-otel.yaml logs --tail=100 keep-anomaly-detector")
    print("  Look for messages containing 'Detected' or 'Alert sent'")


if __name__ == "__main__":
    main()

