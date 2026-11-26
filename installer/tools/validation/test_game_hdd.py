"""Run from a VS x64 Developer Command Prompt with clang-cl and LLD on PATH."""
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[3]
SDK = ROOT / 'ps3recomp'


class GameHddTests(unittest.TestCase):
    def test_native_mount_and_guest_open_handlers(self):
        clang = shutil.which('clang-cl')
        msvc = shutil.which('cl')
        if not clang or not msvc or not shutil.which('link'):
            self.skipTest('VS x64 environment with clang-cl required')
        with tempfile.TemporaryDirectory() as temporary:
            temp = Path(temporary)
            # The game-specific generated header provides this same context ABI.
            (temp / 'ppu_recomp.h').write_text('#include "ppu_context.h"\n')
            common = ['/nologo', '/O1', '/Gy', '/Gw', '/MD',
                      '/D_CRT_SECURE_NO_WARNINGS', '/I' + str(ROOT),
                      '/I' + str(SDK / 'include'), '/I' + str(SDK / 'runtime/ppu'),
                      '/I' + str(temp)]
            for source in [SDK / 'runtime/syscalls/sys_fs.c', SDK / 'libs/filesystem/cellFs.c']:
                subprocess.run([clang, *common, '/clang:-Wno-comment', '/std:c17', '/c',
                                str(source)], cwd=temp, check=True)
            fixture = Path(__file__).with_name('game_hdd_fixture.cpp')
            # Use the compiler shipped with the runner's MSVC STL for the C++ fixture.
            subprocess.run([msvc, *common, '/std:c++20', '/EHsc', '/c', str(fixture)],
                           cwd=temp, check=True)
            subprocess.run([clang, '/nologo', '/MD', '-fuse-ld=lld', '/Fe:game_hdd_fixture.exe',
                            'game_hdd_fixture.obj', 'sys_fs.obj', 'cellFs.obj', '/link', '/opt:ref'],
                           cwd=temp, check=True)
            for scenario, title in [('fresh', 'BCUS98119'), ('legacy', 'BCUS98119'),
                                    ('split', 'BCUS98119'),
                                    ('explicit', 'BCES00220')]:
                with self.subTest(scenario=scenario):
                    run = temp / scenario
                    disc = run / 'disc'
                    relative = Path('game') / title / 'USRDIR'
                    env = os.environ.copy()
                    for key in ('PS3_HDD0_ROOT', 'PS3_USRDIR_BASE', 'PS3_VFS_ROOT', 'CHECK_SPLIT_CACHE'):
                        env.pop(key, None)
                    expected_root = run / 'gamedata/dev_hdd0'
                    if scenario == 'legacy':
                        expected_root = disc
                    if scenario == 'explicit':
                        expected_root = run / 'custom HDD'
                        env['PS3_HDD0_ROOT'] = str(expected_root)
                    (disc / 'PS3_GAME/USRDIR').mkdir(parents=True)
                    (disc / 'PS3_GAME/USRDIR/legacy.bin').write_bytes(b'disc')
                    save = run / 'gamedata/dev_hdd0/home/00000001/savedata/STATE'
                    save.parent.mkdir(parents=True)
                    save.write_bytes(b'save')
                    if scenario != 'fresh':
                        (expected_root / relative).mkdir(parents=True)
                        (expected_root / relative / 'existing').write_bytes(b'cache or DLC')
                    if scenario == 'split':
                        env['CHECK_SPLIT_CACHE'] = '1'
                        (disc / relative).mkdir(parents=True)
                        (disc / relative / 'ppu.bin').write_bytes(b'ppu cache')
                        (expected_root / relative / 'library.bin').write_bytes(b'library cache')
                    subprocess.run([str(temp / 'game_hdd_fixture.exe'), str(disc), title],
                                   cwd=run, env=env, check=True, timeout=15)
                    self.assertTrue((expected_root / relative / 'ne.bv').is_file())
                    for image in ['ICON0.PNG', 'PIC1.PNG']:
                        self.assertTrue((expected_root / relative.parent / image).is_file())
                    self.assertEqual(save.read_bytes(), b'save')
                    if scenario != 'fresh':
                        self.assertEqual((expected_root / relative / 'existing').read_bytes(),
                                         b'cache or DLC')
                    if scenario in ('fresh', 'explicit'):
                        self.assertFalse((disc / 'game').exists())


if __name__ == '__main__':
    unittest.main()
