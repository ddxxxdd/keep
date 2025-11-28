#!/usr/bin/env python3
"""检查异常检测数据"""
import requests
import time
import numpy as np
from collections import defaultdict

metric = 'prometheus_http_requests_total'
url = 'http://prometheus:9090/api/v1/query_range'
end_time = time.time()
start_time = end_time - 3600

params = {'query': metric, 'start': start_time, 'end': end_time, 'step': '60s'}
r = requests.get(url, params=params, timeout=30)
d = r.json()
results = d.get('data', {}).get('result', [])

# 按时间点聚合
time_buckets = defaultdict(float)
for series in results:
    values = series.get('values', [])
    for timestamp, value in values:
        try:
            time_buckets[timestamp] += float(value)
        except:
            pass

sorted_times = sorted(time_buckets.items())
values = np.array([v for _, v in sorted_times])

print(f'指标: {metric}')
print(f'数据点数量: {len(values)}')
print(f'平均值: {values.mean():.2f}')
print(f'标准差: {values.std():.2f}')
print(f'最小值: {values.min():.2f}')
print(f'最大值: {values.max():.2f}')
print(f'最后10个值: {values[-10:].tolist()}')

# 检查最近30%的数据点（与代码中的检测窗口一致）
recent_30pct = values[int(len(values)*0.7):]
print(f'\n最近30%的数据点范围: [{int(len(values)*0.7)}, {len(values)})')
print(f'最近30%的值: {recent_30pct.tolist()}')

# 使用1.5阈值检测异常（当前配置）
mean = values.mean()
std = values.std()
if std > 0:
    z_scores = np.abs((values - mean) / std)
    anomalies = z_scores > 1.5
    recent_anomalies = anomalies[int(len(values)*0.7):]
    
    print(f'\n使用Z-score > 1.5检测（当前配置）:')
    print(f'  所有异常点数量: {np.sum(anomalies)}')
    print(f'  最近30%中的异常点数量: {np.sum(recent_anomalies)}')
    
    if np.sum(recent_anomalies) > 0:
        print(f'  ✓ 应该会触发警报！')
        anomaly_indices = np.where(anomalies[int(len(values)*0.8):])[0]
        print(f'  异常点相对索引: {anomaly_indices.tolist()}')
        print(f'  异常点值: {recent_20pct[anomaly_indices].tolist()}')
        print(f'  异常点Z-score: {z_scores[int(len(values)*0.8):][anomaly_indices].tolist()}')
    else:
        print(f'  ✗ 最近30%中没有异常，不会触发警报')
        print(f'  提示: 需要让异常发生在最近的数据点中')
else:
    print('标准差为0，无法检测异常')

