"""Record a scenario boundary, relative to the collector start when available."""
import argparse
import datetime as dt
import json
from pathlib import Path

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('run', type=Path)
parser.add_argument('label', help='For example street-start, combat-end, or gameplay-start')
args = parser.parse_args()
run = json.loads((args.run / 'run.json').read_text(encoding='utf-8'))
now = dt.datetime.now(dt.timezone.utc)
marker = {'label': args.label, 'utc': now.isoformat(), 'game_pid': run['game_pid']}
try:
    profiler = json.loads((args.run / 'profiler.json').read_text(encoding='utf-8-sig'))
    start = dt.datetime.fromisoformat(profiler['started_utc'].replace('Z', '+00:00'))
    marker['approx_capture_seconds'] = (now - start).total_seconds()
except (OSError, ValueError, KeyError):
    marker['approx_capture_seconds'] = None
with (args.run / 'markers.jsonl').open('a', encoding='utf-8') as stream:
    stream.write(json.dumps(marker) + '\n')
print(json.dumps(marker))
