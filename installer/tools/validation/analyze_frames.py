"""Summarize PresentMon --v1_metrics CSV; frame timing is not gameplay acceptance."""
import argparse
import collections
import csv
import json
import math
import statistics

METRICS = ('msBetweenPresents', 'msBetweenDisplayChange',
           'msUntilRenderComplete', 'msGPUActive')


def summarize(values):
    values = sorted(v for v in values if math.isfinite(v) and v > 0)
    if not values:
        return None
    result = {'frames': len(values), 'mean_ms': statistics.mean(values),
              'max_ms': values[-1]}
    for percentile in (50, 95, 99):
        result[f'p{percentile}_ms'] = values[math.ceil(len(values)*percentile/100)-1]
    for threshold in (20, 50, 100):
        result[f'over{threshold}ms'] = sum(v > threshold for v in values)
    return result


def analyze(path, start=0, end=math.inf, pid=None):
    if not 0 <= start < end:
        raise ValueError('Require 0 <= start < end')
    groups = collections.defaultdict(list)
    with open(path, newline='', encoding='utf-8-sig') as stream:
        reader = csv.DictReader(stream)
        required = {'TimeInSeconds', 'ProcessID', 'SwapChainAddress', 'msBetweenPresents'}
        if not required.issubset(reader.fieldnames or []):
            raise ValueError('Missing PresentMon v1 columns (empty/incomplete/wrong CSV)')
        for row in reader:
            if pid is not None and row['ProcessID'] != str(pid):
                continue
            if start <= float(row['TimeInSeconds']) <= end:
                groups[(row['ProcessID'], row['SwapChainAddress'])].append(row)
    result = []
    for (process, swapchain), rows in groups.items():
        metrics = {}
        for name in METRICS:
            values = []
            for row in rows:
                try:
                    values.append(float(row.get(name, 'NA')))
                except (ValueError, TypeError):
                    pass
            metrics[name] = summarize(values)
        present = metrics['msBetweenPresents']
        if present is None:
            continue
        result.append({'pid': process, 'swapchain': swapchain,
                       'first_s': float(rows[0]['TimeInSeconds']),
                       'last_s': float(rows[-1]['TimeInSeconds']),
                       'dropped': sum(r.get('Dropped') == '1' for r in rows),
                       'metrics': metrics,
                       'mean_fps': 1000 / present['mean_ms'],
                       'p99_target_met': present['p99_ms'] <= 20})
    if not result:
        raise ValueError('No valid frames in selected process/time interval')
    return result


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('csv')
    parser.add_argument('--start', type=float, default=0)
    parser.add_argument('--end', type=float, default=math.inf)
    parser.add_argument('--pid', type=int)
    args = parser.parse_args()
    print(json.dumps(analyze(args.csv, args.start, args.end, args.pid), indent=2))
