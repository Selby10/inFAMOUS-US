"""Exercise guest trophy unlocks against temporary local trophy data."""
import json
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[3]


class TrophyTests(unittest.TestCase):
    def test_guest_unlocks_persist_and_queue_one_notice_each(self):
        compiler = shutil.which("clang-cl")
        if not compiler or not shutil.which("link"):
            self.skipTest("VS x64 environment with clang-cl required")
        with tempfile.TemporaryDirectory(prefix="infamous-trophies-") as temporary:
            work = Path(temporary)
            storage = work / "gamedata/trophies"
            storage.mkdir(parents=True)
            history = storage / "NPWR00001.json"
            history.write_text('{ "trophies": [ { "id": 1, "timestamp": 123 } ] }', encoding="utf-8")
            (storage / "catalog.tsv").write_text(
                "TROPHIES1\tNPWR00001\t0\n"
                "0\tP\t-1\t0\tPlatinum\tCollect all trophies\n"
                "1\tB\t0\t0\tFirst trophy\tA trophy\n"
                "3\tG\t0\t1\tA hidden trophy\tKeep it secret\n",
                encoding="utf-8",
            )
            test = work / "trophies.exe"
            command = [
                compiler, "/nologo", "/std:c17", "/D_CRT_SECURE_NO_WARNINGS",
                "/I" + str(ROOT / "ps3recomp/include"),
                str(ROOT / "ps3recomp/libs/network/tests/test_trophy_achievements.c"),
                "/Fe:" + str(test),
            ]
            compiled = subprocess.run(command, cwd=work, capture_output=True, text=True)
            self.assertEqual(compiled.returncode, 0, compiled.stdout + compiled.stderr)
            ran = subprocess.run([str(test)], cwd=work, capture_output=True, text=True, timeout=20)
            self.assertEqual(ran.returncode, 0, ran.stdout + ran.stderr)
            self.assertIn("Named trophy notices, platinum, and persistent progress passed", ran.stdout)
            trophies = json.loads(history.read_text(encoding="utf-8"))["trophies"]
            self.assertEqual([row["id"] for row in trophies], [0, 1, 3])
            self.assertEqual(trophies[1]["timestamp"], 123)
            self.assertFalse(history.with_suffix(".json.tmp").exists())
