import csv
import math
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from analyze_frames import analyze, summarize
from run_validation import launch_environment, configure_scripted_input


class ValidationTests(unittest.TestCase):
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
        with patch.dict(os.environ, {'D3D12_DBG': '1', 'D3D12_IQ': '1'}):
            normal = launch_environment(manifest, hdd, 'normal', [])
        self.assertNotIn('D3D12_DBG', normal)
        self.assertNotIn('D3D12_IQ', normal)
        debug = launch_environment(manifest, hdd, 'debug', [])
        self.assertEqual(debug['D3D12_DBG'], '1')
        self.assertEqual(debug['D3D12_DRED'], '1')
        timing = launch_environment(manifest, hdd, 'timing', ['RF_ASYNC=0'])
        self.assertEqual(normal['D3D12_DRED'], '0')
        self.assertEqual(timing['PS3_TELEM'], '1')
        self.assertEqual(timing['RF_ASYNC'], '0')
        self.assertEqual(manifest['launch']['env']['RF_ASYNC'], '1')
        parallel = launch_environment(manifest, hdd, 'timing', ['TEXCS=1', 'TEXCS_THREADS=2'])
        self.assertEqual(parallel['TEXCS'], '1')
        self.assertEqual(parallel['TEXCS_THREADS'], '2')
        hitch = launch_environment(manifest, hdd, 'timing', ['HITCH_MS=60', 'HITCH_MAX=8', 'SPIKE_MS=50', 'SPIKE_MAX=40'])
        self.assertEqual(hitch['HITCH_MS'], '60')
        self.assertEqual(hitch['SPIKE_MAX'], '40')
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
