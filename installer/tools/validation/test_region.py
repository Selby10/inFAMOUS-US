import importlib.util
from pathlib import Path
import struct
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[3]
spec = importlib.util.spec_from_file_location('detect_region', ROOT/'infamous-recomp/tools/detect_region.py')
region = importlib.util.module_from_spec(spec)
spec.loader.exec_module(region)


def sfo(title):
    value = title.encode() + b'\0'
    return (struct.pack('<5I', 0x46535000, 0x101, 36, 45, 1) +
            struct.pack('<HHIII', 0, 0x204, len(value), len(value), 0) + b'TITLE_ID\0' + value)


class RegionTests(unittest.TestCase):
    def test_supported_and_rejected_sfos(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp)/'PARAM.SFO'
            for title in ('BCUS98119', 'BCES00220'):
                p.write_bytes(sfo(title))
                self.assertEqual(region.title_id(p), title)
            for data in (b'', sfo('OTHER'), sfo('BCUS98119')[:-1],
                         sfo('BCUS98119')[:12] + b'\xff'*4 + sfo('BCUS98119')[16:]):
                p.write_bytes(data)
                with self.assertRaises(ValueError): region.title_id(p)

    def test_discovery_ambiguity_and_explicit_selection(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            with self.assertRaises(ValueError): region.select_sfo(root)
            us = root/'Infamous (US)/PS3_GAME/PARAM.SFO'
            us.parent.mkdir(parents=True); us.write_bytes(sfo('BCUS98119'))
            self.assertEqual(region.select_sfo(root), 'BCUS98119')
            eu = root/'Infamous (Europe) (Fr,De,Es,It,Nl)/PS3_GAME/PARAM.SFO'
            eu.parent.mkdir(parents=True); eu.write_bytes(sfo('BCES00220'))
            with self.assertRaises(ValueError): region.select_sfo(root)
            self.assertEqual(region.select_sfo(root, eu), 'BCES00220')
            with self.assertRaises(FileNotFoundError): region.select_sfo(root, root/'missing')
