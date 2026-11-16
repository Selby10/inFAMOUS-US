"""Six repeatable idle-checkpoint captures. Use an isolated HDD/save copy."""
import argparse
import json
from pathlib import Path
import subprocess
import sys

from analyze_frames import analyze


def accepted(record, frames):
    return (record.get('status') == 'completed' and
            record.get('performance_mode_verified') is True and
            record.get('device_failure') is False and
            not record.get('forced_cleanup') and
            record.get('collector_exit_code') == 0 and
            record.get('capture_status', '').startswith('completed') and bool(frames) and
            all(frame.get('p99_target_met') is True and frame['first_s'] <= 32 and
                frame['last_s'] >= 175 for frame in frames))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('root', 'elf', 'hdd-root', 'output-root', 'presentmon'):
        parser.add_argument('--' + name, type=Path, required=True)
    args = parser.parse_args()
    args.output_root.mkdir(parents=True, exist_ok=True)
    results = []
    runner = Path(__file__).with_name('run_validation.py')
    for cache in ('cold', 'warm'):
        for repeat in range(1, 4):
            command = [sys.executable, str(runner)]
            for name in ('root', 'elf', 'hdd-root', 'output-root', 'presentmon'):
                command.extend(['--' + name, str(getattr(args, name.replace('-', '_')))])
            command.extend(['--scenario', f'idle-{cache}-{repeat}', '--cache', cache,
                            '--mode', 'normal', '--seconds', '180', '--auto-close',
                            '--collector-direct', '--scripted-input', '--hold-neutral',
                            '--env', 'PAD_KBM=0'])
            print(f'Starting {cache} {repeat}/3', flush=True)
            child = subprocess.run(command, capture_output=True, text=True)
            if child.returncode:
                raise RuntimeError(child.stdout + child.stderr)
            run = Path(child.stdout.strip().splitlines()[0])
            record = json.loads((run/'run.json').read_text())
            capture_error = None
            try:
                frames = analyze(run/'frames.csv', 30, 180, record['game_pid'])
            except (OSError, ValueError) as error:
                frames = []
                capture_error = str(error)
            result = dict(run=str(run), cache=cache, repeat=repeat,
                          accepted=accepted(record, frames), frames=frames,
                          status=record['status'], capture_error=capture_error)
            results.append(result)
            (args.output_root/'matrix-summary.json').write_text(json.dumps(dict(
                results=results, complete=len(results) == 6,
                idle_acceptance=len(results) == 6 and all(r['accepted'] for r in results),
                gameplay_routes='pending manual acceptance'), indent=2))
            print(f"Finished {cache} {repeat}/3: accepted={result['accepted']} ({run})", flush=True)
            if capture_error or record.get('collector_exit_code') != 0 or record.get('device_failure') or record.get('forced_cleanup'):
                raise RuntimeError('Stop matrix after device failure or failed normal closure')


if __name__ == '__main__':
    main()
