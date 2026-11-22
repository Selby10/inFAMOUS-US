from contextlib import chdir, redirect_stderr, redirect_stdout
import csv
import hashlib
import io
import json
import math
import os
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from analyze_frames import analyze, summarize
from analyze_host_frames import (BACKGROUND_SCOPE, FOREGROUND_SCOPE, FLAG_CLOCK_ERROR, FLAG_FULL,
                                 FLAG_INCLUDE_BACKGROUND, HEADER, SAMPLE, analyze_host_frames)
from run_validation import (HOST_BACKGROUND_ENV, HOST_FRAME_ENV, configure_scripted_input,
                            launch_environment, main, write_json)


class ValidationTests(unittest.TestCase):
    def test_run_launches_and_records_selected_executable(self):
        for use_override in (False, True):
            with self.subTest(use_override=use_override), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp).resolve()
                port = root / 'infamous-recomp'
                default_exe = port / 'build-install/inFamousRecomp.exe'
                candidate = Path('candidate build/inFamousCandidate.exe')
                exe = root / candidate if use_override else default_exe
                port.mkdir()
                exe.parent.mkdir()
                exe.write_bytes(b'candidate' if use_override else b'baseline')
                settings = 'VSYNC=0\n'
                (port / 'settings.ini').write_text(settings)
                (root / 'installer').mkdir()
                (root / 'installer/infamous.toml').write_text('[launch.env]\nRF_ASYNC = "1"\n')
                elf = root / 'EBOOT.elf'
                elf.touch()
                hdd = root / 'HDD'
                hdd.mkdir()
                output = root / 'captures'
                argv = ['run_validation.py', '--root', str(root), '--elf', str(elf),
                        '--hdd-root', str(hdd), '--output-root', str(output),
                        '--scenario', 'idle', '--host-frames']
                if use_override:
                    argv += ['--exe', str(Path(root.name) / candidate)]

                def launch(command, *, cwd, env, **kwargs):
                    self.assertEqual(command, [str(exe), str(elf)])
                    self.assertEqual(cwd, port)
                    self.assertEqual(env['PS3_HDD0_ROOT'], str(hdd))
                    self.assertEqual(env['SAVEDATA_ROOT'], str(hdd / 'home/00000001/savedata'))
                    self.host_capture(Path(env[HOST_FRAME_ENV]).parent, [(100, 1), (116, 2)])
                    return SimpleNamespace(pid=42, returncode=0, poll=lambda: 0)

                popen = Mock(side_effect=launch)
                # Python 3.11 uses the real subprocess module to discover the Windows version.
                validation_subprocess = SimpleNamespace(CREATE_NO_WINDOW=0, Popen=popen)
                with (chdir(root.parent), patch.object(sys, 'argv', argv), redirect_stdout(io.StringIO()),
                      patch('run_validation.command_output', return_value='') as command_output,
                      patch('run_validation.subprocess', validation_subprocess)):
                    main()
                popen.assert_called_once()
                image_filters = {call.args[0][2].casefold() for call in command_output.call_args_list
                                 if call.args[0][0] == 'tasklist'}
                expected_filters = {f'imagename eq {path.name.casefold()}' for path in (default_exe, exe)}
                self.assertEqual(image_filters, expected_filters)
                run, = output.iterdir()
                record = json.loads((run / 'run.json').read_text(encoding='utf-8'))
                self.assertEqual(record['status'], 'completed')
                self.assertEqual(record['capture_status'], 'completed; host-supplied Presents only')
                self.assertEqual(record['game_pid'], 42)
                self.assertEqual(record['executable'], str(exe))
                self.assertEqual(record['executable_sha256'], hashlib.sha256(exe.read_bytes()).hexdigest())
                self.assertEqual((run / 'settings.ini').read_text(), settings)
                summary = json.loads((run / 'host-frames-summary.json').read_text(encoding='utf-8'))
                self.assertEqual(summary['game_pid'], record['game_pid'])

    def test_run_rejects_missing_executable_or_directory(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            directory = root / 'candidate'
            directory.mkdir()
            default = root / 'infamous-recomp/build-install/inFamousRecomp.exe'
            default.mkdir(parents=True)
            missing = root / 'missing.exe'
            required = ['run_validation.py', '--root', str(root), '--elf', str(root),
                        '--hdd-root', str(root), '--output-root', str(root), '--scenario', 'idle']
            for exe, extra in ((directory, ['--exe', str(directory)]),
                               (missing, ['--exe', str(missing)]), (default, [])):
                with (self.subTest(exe=exe), patch.object(sys, 'argv', required + extra),
                      redirect_stderr(io.StringIO()) as stderr,
                      patch('run_validation.command_output') as command_output):
                    with self.assertRaises(SystemExit) as raised:
                        main()
                    self.assertEqual(raised.exception.code, 2)
                    self.assertIn(f'Game executable is not a file: {exe}', stderr.getvalue())
                    command_output.assert_not_called()

    @unittest.skipUnless(os.name == 'nt', 'Windows file sharing semantics')
    def test_json_replacement_recovers_when_reader_closes(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'run.json'
            write_json(path, {'status': 'starting'})
            with path.open(encoding='utf-8') as reader:
                def close_reader(_delay):
                    self.assertEqual(json.load(reader), {'status': 'starting'})
                    reader.close()

                with patch('run_validation.time.sleep', side_effect=close_reader):
                    write_json(path, {'status': 'running'})
            self.assertEqual(json.loads(path.read_text(encoding='utf-8')), {'status': 'running'})

    @unittest.skipUnless(os.name == 'nt', 'Windows file sharing semantics')
    def test_json_replacement_raises_after_retry_limit_when_reader_stays_open(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'run.json'
            write_json(path, {'status': 'starting'})
            with path.open(encoding='utf-8') as reader:
                with patch('run_validation.time.sleep', side_effect=[None] * 10) as delay:
                    with self.assertRaises(PermissionError) as raised:
                        write_json(path, {'status': 'running'})
                self.assertIn(raised.exception.winerror, (5, 32, 33))
                self.assertEqual(delay.call_count, 10)
                self.assertEqual(json.load(reader), {'status': 'starting'})

    def host_capture(self, directory, samples, flags=0, committed=None):
        path = Path(directory) / 'host-frames.bin'
        capacity = len(samples) if flags & FLAG_FULL else 16
        count = len(samples) if committed is None else committed
        header = HEADER.pack(b'INFPRES1', 1000, 100, 116444736000000000, capacity, count, flags, 42)
        data = b''.join(SAMPLE.pack(*sample) for sample in samples)
        path.write_bytes(header + data.ljust(capacity * SAMPLE.size, b'\x00'))
        return path

    def test_host_intervals_exclude_gap_and_range_edges(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = self.host_capture(tmp, [(100, 1), (116, 2), (132, 3), (1000, 7), (1064, 8), (1100, 9)],
                                     committed=5)
            result = analyze_host_frames(path, start=0.01, end=0.97, pid=42)
            self.assertEqual(result['source'], 'host_dxgi_present')
            self.assertEqual(result['timing_scope'], FOREGROUND_SCOPE)
            self.assertEqual(result['status'], 'completed')
            self.assertEqual(result['capture_started_utc'], '1970-01-01T00:00:00+00:00')
            self.assertEqual(result['recorded_presents'], 5)
            self.assertEqual(result['selected_presents'], 4)
            self.assertEqual(result['excluded_gap_intervals'], 1)
            self.assertEqual(result['first_s'], 0.016)
            self.assertEqual(result['last_s'], 0.964)
            metrics = result['metrics']['host_present_interval']
            self.assertEqual(metrics['frames'], 2)
            self.assertEqual(metrics['p50_ms'], 16)
            self.assertEqual(metrics['p95_ms'], 64)
            self.assertEqual(metrics['p99_ms'], 64)
            self.assertEqual(metrics['over20ms'], 1)
            self.assertEqual(metrics['over50ms'], 1)
            self.assertEqual(metrics['over100ms'], 0)

    def test_host_capture_reports_overflow_and_clock_failure(self):
        with tempfile.TemporaryDirectory() as tmp:
            for flag, warning in ((FLAG_FULL, 'capture capacity exhausted'),
                                  (FLAG_CLOCK_ERROR, 'performance counter failed')):
                path = self.host_capture(tmp, [(100, 1), (116, 2)], flag)
                result = analyze_host_frames(path)
                self.assertEqual(result['status'], 'partial')
                self.assertEqual(result['warnings'], [warning])

    def test_background_scope_survives_capture_failure_flags(self):
        with tempfile.TemporaryDirectory() as tmp:
            samples = [(100, 1), (116, 2)]
            for flags in (0, FLAG_FULL, FLAG_CLOCK_ERROR):
                path = self.host_capture(tmp, samples, flags | FLAG_INCLUDE_BACKGROUND)
                result = analyze_host_frames(path)
                self.assertEqual(result['timing_scope'], BACKGROUND_SCOPE)
                self.assertEqual(result['status'], 'partial' if flags else 'completed')

    def test_host_capture_rejects_unusable_and_corrupt_data(self):
        with tempfile.TemporaryDirectory() as tmp:
            for samples in ([], [(100, 1)], [(100, 1), (110, 3)]):
                path = self.host_capture(tmp, samples)
                with self.assertRaisesRegex(ValueError, 'No usable'):
                    analyze_host_frames(path)
            path = self.host_capture(tmp, [(100, 1), (120, 2)])
            with self.assertRaisesRegex(ValueError, 'range|start'):
                analyze_host_frames(path, start=math.nan)
            with self.assertRaisesRegex(ValueError, 'PID'):
                analyze_host_frames(path, pid=99)
            path.write_bytes(path.read_bytes()[:-1])
            with self.assertRaisesRegex(ValueError, 'wrong size'):
                analyze_host_frames(path)
            path = self.host_capture(tmp, [(101, 1), (100, 2)])
            with self.assertRaisesRegex(ValueError, 'corrupt sample'):
                analyze_host_frames(path)

    def test_percentiles_ignore_missing_measurements(self):
        result = summarize([0, -1, math.nan, math.inf] + [16.0]*99 + [50.0])
        self.assertEqual(result['frames'], 100)
        self.assertEqual(result['p99_ms'], 16)
        self.assertEqual(result['max_ms'], 50)
        self.assertEqual(result['over20ms'], 1)

    def test_separate_swapchains_filter_pid_and_interval(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'frames.csv'
            with path.open('w', newline='') as stream:
                writer = csv.writer(stream)
                writer.writerow(['TimeInSeconds', 'ProcessID', 'SwapChainAddress', 'msBetweenPresents', 'Dropped'])
                writer.writerows([[0, 1, 'A', 900, 0], [31, 1, 'A', 16, 0],
                                  [32, 1, 'A', 32, 1], [32, 2, 'A', 800, 0],
                                  [33, 1, 'B', 10, 0], [100, 1, 'A', 900, 0]])
            result = analyze(path, 30, 40, 1)
            self.assertEqual(len(result), 2)
            self.assertEqual(result[0]['metrics']['msBetweenPresents']['mean_ms'], 24)
            self.assertEqual(result[0]['dropped'], 1)
            self.assertFalse(result[0]['p99_target_met'])
            self.assertTrue(result[1]['p99_target_met'])
            with self.assertRaises(ValueError):
                analyze(path, 200, 300)

    def test_empty_and_wrong_schema_fail(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'frames.csv'
            for content in ('', 'TimeInSeconds,Other\n0,1\n'):
                path.write_text(content)
                with self.assertRaises(ValueError):
                    analyze(path)

    def test_run_local_diagnostics_and_overrides(self):
        manifest = {'launch': {'env': {'PS3_TELEM': '0', 'GPUPROF': '0', 'PERF': '0', 'RF_ASYNC': '1'}}}
        hdd = Path('private-hdd')
        with patch.dict(os.environ, {'D3D12_DBG': '1', 'D3D12_IQ': '1', HOST_FRAME_ENV: 'stale.bin',
                                     HOST_BACKGROUND_ENV: '1', 'INFAMOUS_FPS_HIRES': '1'}):
            normal = launch_environment(manifest, hdd, 'normal', [])
        self.assertNotIn('D3D12_DBG', normal)
        self.assertNotIn('D3D12_IQ', normal)
        self.assertNotIn(HOST_FRAME_ENV, normal)
        self.assertNotIn(HOST_BACKGROUND_ENV, normal)
        self.assertNotIn('INFAMOUS_FPS_HIRES', normal)
        debug = launch_environment(manifest, hdd, 'debug', [])
        self.assertEqual(debug['D3D12_DBG'], '1')
        self.assertEqual(debug['D3D12_DRED'], '1')
        timing = launch_environment(manifest, hdd, 'timing', ['RF_ASYNC=0'])
        self.assertEqual(normal['D3D12_DRED'], '0')
        self.assertEqual(timing['PS3_TELEM'], '1')
        self.assertEqual(timing['RF_ASYNC'], '0')
        hires = launch_environment(manifest, hdd, 'normal', ['INFAMOUS_FPS_HIRES=1'])
        self.assertEqual(hires['INFAMOUS_FPS_HIRES'], '1')
        self.assertEqual(manifest['launch']['env']['RF_ASYNC'], '1')
        parallel = launch_environment(manifest, hdd, 'timing', ['TEXCS=1', 'TEXCS_THREADS=2'])
        self.assertEqual(parallel['TEXCS'], '1')
        self.assertEqual(parallel['TEXCS_THREADS'], '2')
        hitch = launch_environment(manifest, hdd, 'timing', ['HITCH_MS=60', 'HITCH_MAX=8', 'SPIKE_MS=50', 'SPIKE_MAX=40'])
        self.assertEqual(hitch['HITCH_MS'], '60')
        self.assertEqual(hitch['SPIKE_MAX'], '40')
        poll = launch_environment(manifest, hdd, 'normal', ['PS3_POLLTOP=8'])
        self.assertEqual(poll['PS3_POLLTOP'], '8')
        with self.assertRaises(ValueError):
            launch_environment(manifest, hdd, 'normal', ['UNKNOWN=1'])

    def test_manual_capture_cannot_hold_physical_sticks(self):
        manifest = {'launch': {'env': {}}}
        injected = {'PAD_FILE': 'old.txt', 'PAD_SCRIPT': '0:8',
                    'PAD_AUTOPRESS': '1', 'PAD_STICK': '0,0,0,0'}
        with patch.dict(os.environ, injected):
            env = launch_environment(manifest, Path('hdd'), 'normal', [])
        self.assertTrue(all(key not in env for key in injected))
        with tempfile.TemporaryDirectory() as tmp:
            run = Path(tmp)
            configure_scripted_input(env, run)
            self.assertNotIn('PAD_FILE', env)
            self.assertFalse((run / 'pad.txt').exists())
            configure_scripted_input(env, run, True)
            self.assertEqual(env['PAD_FILE'], str(run / 'pad.txt'))
            self.assertEqual((run / 'pad.txt').read_text(), '')


if __name__ == '__main__':
    unittest.main()
