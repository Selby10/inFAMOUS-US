"""Exercise real savedata callbacks and slot metadata without player saves."""
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[3]


class SaveSlotTests(unittest.TestCase):
    def test_manual_slots_and_autosave_are_independent(self):
        compiler = shutil.which('clang-cl')
        if not compiler or not shutil.which('link'):
            self.skipTest('VS x64 environment with clang-cl required')
        with tempfile.TemporaryDirectory(prefix='infamous-save-slots-') as temporary:
            output = Path(temporary) / 'slots.exe'
            command = [compiler, '/nologo', '/std:c17', '/D_CRT_SECURE_NO_WARNINGS',
                       '/I' + str(ROOT / 'ps3recomp/include'),
                       str(ROOT / 'ps3recomp/libs/system/tests/test_savedata_slots.c'),
                       '/Fe:' + str(output), 'user32.lib', 'gdi32.lib']
            compiled = subprocess.run(command, cwd=temporary, capture_output=True, text=True)
            self.assertEqual(compiled.returncode, 0, compiled.stdout + compiled.stderr)
            result = subprocess.run([str(output)], cwd=temporary,
                                    capture_output=True, text=True, timeout=20)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertIn('Multi-slot callback/file IO and autosave isolation passed', result.stdout)
