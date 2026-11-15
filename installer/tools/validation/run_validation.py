"""Launch a manifest-configured native run and retain a private evidence bundle.

Requires Python 3.11+. Close the game normally to finalize the bundle.
"""
import argparse
import datetime as dt
import hashlib
import json
import os
from pathlib import Path
import platform
import shutil
import subprocess
import time
import tomllib

from analyze_frames import analyze

# Existing renderer experiments, deliberately opt-in and recorded per run.
PROFILE_OVERRIDES = {'TEXCS', 'TEXCS_THREADS', 'TEXCS_EARLY', 'TEXCS_PHASE',
                     'TEXCS_PHASE_DIV', 'TEXCS_ECORE', 'TEX_FAST_DECODE',
                     'HITCH_MS', 'HITCH_MAX', 'SPIKE_MS', 'SPIKE_MAX'}


def utc():
    return dt.datetime.now(dt.timezone.utc).isoformat()


def write_json(path, value):
    temporary = path.with_suffix('.tmp')
    temporary.write_text(json.dumps(value, indent=2), encoding='utf-8')
    temporary.replace(path)


def command_output(args, cwd=None):
    try:
        result = subprocess.run(args, cwd=cwd, capture_output=True, text=True,
                                timeout=15, creationflags=subprocess.CREATE_NO_WINDOW)
        return result.stdout.strip() if result.returncode == 0 else 'unavailable'
    except (OSError, subprocess.TimeoutExpired):
        return 'unavailable'


def launch_environment(manifest, hdd, mode, overrides):
    env = os.environ.copy()
    env.update(manifest['launch']['env'])
    # These are run-local controls, never inherited from an earlier experiment.
    env.update(PS3_HDD0_ROOT=str(hdd),
               SAVEDATA_ROOT=str(hdd / 'home/00000001/savedata'),
               D3D12_DRED='0', PS3_TELEM='0', GPUPROF='0', PERF='0')
    # These older runtime switches are enabled by presence, including "0".
    env.pop('D3D12_DBG', None)
    env.pop('D3D12_IQ', None)
    # A manual capture must never inherit bench input or a persistent stick hold.
    for key in ('PAD_FILE', 'PAD_SCRIPT', 'PAD_AUTOPRESS', 'PAD_STICK'):
        env.pop(key, None)
    if mode in ('dred', 'debug'):
        env['D3D12_DRED'] = '1'
    if mode == 'debug':
        env.update(D3D12_DBG='1', D3D12_IQ='1')
    if mode == 'timing':
        env.update(PS3_TELEM='1', GPUPROF='1', PERF='1')
    for item in overrides:
        key, separator, value = item.partition('=')
        if not separator or (key not in manifest['launch']['env'] and key not in PROFILE_OVERRIDES):
            raise ValueError('Overrides must name a manifest variable or documented profiling experiment: ' + item)
        env[key] = value
    return env


def configure_scripted_input(env, run, enabled=False):
    env.pop('PAD_FILE', None)
    if enabled:
        # Empty means no injected input. A neutral stick command would HOLD
        # both sticks at centre, overriding the physical pad indefinitely.
        path = run / 'pad.txt'
        path.write_text('', encoding='utf-8')
        env['PAD_FILE'] = str(path)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, required=True)
    parser.add_argument('--elf', type=Path, required=True)
    parser.add_argument('--hdd-root', type=Path, required=True)
    parser.add_argument('--output-root', type=Path, required=True)
    parser.add_argument('--presentmon', type=Path)
    parser.add_argument('--powershell', default=shutil.which('pwsh') or 'powershell.exe',
                        help='PowerShell host allowed to execute local scripts; no policy is changed')
    parser.add_argument('--seconds', type=int, default=1800)
    parser.add_argument('--scenario', required=True)
    parser.add_argument('--mode', choices=('normal', 'timing', 'dred', 'debug'), default='normal')
    parser.add_argument('--cache', choices=('warm', 'cold'), default='warm')
    parser.add_argument('--env', action='append', default=[])
    parser.add_argument('--scripted-input', action='store_true',
                        help='Opt in to an initially empty PAD_FILE command channel for automated input')
    args = parser.parse_args()
    if args.seconds < 1:
        parser.error('--seconds must be positive')
    root = args.root.resolve(strict=True)
    port = root / 'infamous-recomp'
    exe = port / 'build-install/inFamousRecomp.exe'
    for required in (exe, args.elf, args.hdd_root):
        if not required.exists():
            parser.error(f'Missing {required}')
    if args.presentmon and not args.presentmon.is_file():
        parser.error('PresentMon executable not found')
    processes = command_output(['tasklist', '/FI', 'IMAGENAME eq inFamousRecomp.exe', '/FO', 'CSV'])
    if 'inFamousRecomp.exe' in processes or processes == 'unavailable':
        parser.error('Close existing game processes before starting validation')
    manifest = tomllib.loads((root / 'installer/infamous.toml').read_text(encoding='utf-8'))
    env = launch_environment(manifest, args.hdd_root.resolve(), args.mode, args.env)
    run = args.output_root.resolve() / dt.datetime.now().strftime('%Y%m%d-%H%M%S-%f')
    run.mkdir(parents=True)
    configure_scripted_input(env, run, args.scripted_input)
    settings_path = port / 'settings.ini'
    settings = settings_path.read_text() if settings_path.exists() else ''
    (run / 'settings.ini').write_text(settings)
    recorded_env = {key: env[key] for key in manifest['launch']['env']}
    for key in PROFILE_OVERRIDES:
        recorded_env[key] = env.get(key)
    for key in ('D3D12_DRED', 'PS3_HDD0_ROOT', 'SAVEDATA_ROOT', 'PAD_FILE'):
        recorded_env[key] = env.get(key)
    for key in ('D3D12_DBG', 'D3D12_IQ'):
        recorded_env[key] = env.get(key)  # null means deliberately unset
    record = dict(schema_version=1, status='starting', created_utc=utc(), scenario=args.scenario,
                  mode=args.mode, cache=args.cache, requested_seconds=args.seconds,
                  executable=str(exe), executable_sha256=hashlib.sha256(exe.read_bytes()).hexdigest(),
                  source_commit=command_output(['git', 'rev-parse', 'HEAD'], root),
                  source_changes=command_output(['git', 'diff', '--stat'], root),
                  os=platform.platform(), environment=recorded_env,
                  gpu=command_output(['nvidia-smi', '--query-gpu=name,driver_version', '--format=csv']),
                  cpu_temperature='not collected', gameplay_acceptance='pending manual validation')
    write_json(run / 'run.json', record)
    print(str(run), flush=True)
    # Cache renames are confined to this resolved port directory; never delete caches.
    cache = port / 'shadercache'
    backup = port / ('shadercache-before-' + run.name)
    cold_output = port / ('shadercache-after-' + run.name)
    game = collector = None
    moved_cache = False
    cold_prepared = False
    try:
        if args.cache == 'cold' and cache.exists():
            cache.rename(backup)
            moved_cache = True
        cold_prepared = args.cache == 'cold'
        with (run / 'game.out.log').open('w') as stdout, (run / 'game.err.log').open('w') as stderr:
            game = subprocess.Popen([str(exe), str(args.elf.resolve())], cwd=port, env=env,
                                    stdout=stdout, stderr=stderr, creationflags=subprocess.CREATE_NO_WINDOW)
            record.update(status='running', game_pid=game.pid, launched_utc=utc())
            write_json(run / 'run.json', record)
            if args.presentmon:
                with (run / 'profiler-launch.log').open('w') as collector_log:
                    collector = subprocess.Popen([args.powershell, '-NoProfile', '-File',
                        str(Path(__file__).resolve().with_name('collect_presentmon.ps1')), '-PresentMon',
                        str(args.presentmon.resolve()), '-GameProcessId', str(game.pid),
                        '-Seconds', str(args.seconds), '-RunDirectory', str(run)],
                        stdout=collector_log, stderr=subprocess.STDOUT,
                        creationflags=subprocess.CREATE_NO_WINDOW)
            started = time.monotonic()
            with (run / 'gpu.csv').open('w') as gpu:
                gpu.write('timestamp,name,utilization.gpu,memory.used,temperature.gpu,power.draw,clocks.gr\n')
                while game.poll() is None:
                    if time.monotonic() - started <= args.seconds:
                        gpu.write(command_output(['nvidia-smi',
                            '--query-gpu=timestamp,name,utilization.gpu,memory.used,temperature.gpu,power.draw,clocks.gr',
                            '--format=csv,noheader']) + '\n')
                        gpu.flush()
                    time.sleep(2)
            record.update(game_exit_code=game.returncode, ended_utc=utc(),
                          elapsed_seconds=time.monotonic()-started)
        record['status'] = 'finalizing'
        write_json(run / 'run.json', record)
        if collector:
            try:
                # Some PresentMon versions finish at --timed even after process
                # exit. Wait for that flush rather than misclassify a locked CSV.
                record['collector_exit_code'] = collector.wait(timeout=args.seconds + 30)
            except subprocess.TimeoutExpired:
                record['collector_error'] = 'Collector still running; CSV may be locked/incomplete'
        logs = (run / 'game.out.log').read_text(errors='replace') + (run / 'game.err.log').read_text(errors='replace')
        record['dred_observed'] = ('enabled' if '[D3D12] DRED enabled' in logs else
                                   'disabled' if '[D3D12] DRED disabled' in logs else 'not reported')
        record['debug_layer_observed'] = '[D3D12] Debug layer enabled' in logs
        record['performance_mode_verified'] = (args.mode == 'normal' and
            record['dred_observed'] == 'disabled' and not record['debug_layer_observed'] and
            all(env[key] == '0' for key in ('PS3_TELEM', 'GPUPROF', 'PERF')))
        record['device_failure'] = 'PRESENT FAILED' in logs or 'FENCE STUCK' in logs or 'wait_for_gpu STUCK' in logs
        record['status'] = 'completed' if game.returncode == 0 else 'game_failed'
        if record['device_failure']:
            record['status'] = 'device_failed'
        if args.presentmon:
            try:
                profiler = json.loads((run / 'profiler.json').read_text(encoding='utf-8-sig'))
                if profiler.get('status') != 'completed' or record.get('collector_exit_code') != 0:
                    raise ValueError('Profiler did not complete successfully')
                write_json(run / 'frames-summary.json', analyze(run / 'frames.csv', pid=game.pid))
                record['capture_status'] = 'completed; includes startup, select gameplay intervals separately'
            except (OSError, ValueError) as error:
                record['capture_status'] = 'failed: ' + str(error)
        else:
            record['capture_status'] = 'not requested; logs only'
    except BaseException as error:
        record.update(status='interrupted', error=str(error))
        raise
    finally:
        # Never restore a cache while its game can still write to it.
        if game is not None and game.poll() is None:
            game.terminate()
            game.wait(timeout=15)
        if cold_prepared:
            if cache.exists():
                cache.rename(cold_output)
                record['cold_cache_output'] = str(cold_output)
            if moved_cache:
                backup.rename(cache)
            record['original_cache_restored'] = True
        write_json(run / 'run.json', record)


if __name__ == '__main__':
    main()
