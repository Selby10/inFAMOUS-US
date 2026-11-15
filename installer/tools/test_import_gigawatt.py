"""Synthetic fixtures only: no game payload or user license material."""
import base64
from pathlib import Path
import tempfile
import unittest

import import_gigawatt as dlc


# Single block containing bytes 0..29, encrypted with the synthetic RIF 00..0f.
FIXTURE = base64.b64decode(
    "TlBEAAAAAAIAAAACAAAAAFVQOTAwMC1CQ1VTOTgxMTlfMDAtU01MQVVOQ0hQUk9NTzAwMAAAAAAAAAAAAAAAAAABAgMEBQYHCAkKCwwNDg8AAAAAAAAAAAAAAAAAAAAAEBESExQVFhcYGRobHB0eHwAAAAAAAAAAAAAAAAAAAAAAAAAMAABAAAAAAAAAAAAeAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAGfaZWS/fupsaf+Ht7aAP+w1S7KQLWwch82rh/jJXUhzz/LiOo3r0tZnVRiXarpH9wAAAAAAAAAAAAAAAAAAAAA="
)


class ImportTests(unittest.TestCase):
    def test_synthetic_license_conversion(self):
        self.assertEqual(dlc.rap_to_rif(bytes(16)).hex(),
                         "a0b21c84d2ef63699e75c34eed055380")
        for length in [0, 15, 17, 32]:
            with self.assertRaises(ValueError):
                dlc.rap_to_rif(bytes(length))

    def test_authenticated_payload(self):
        self.assertEqual(dlc.validate_sentinel(FIXTURE, bytes(range(16))), bytes(range(30)))

    def test_wrong_license_and_corruption_rejected(self):
        for offset in [20, 128, 256, 280]:
            damaged = bytearray(FIXTURE)
            damaged[offset] ^= 1
            with self.subTest(offset=offset), self.assertRaises(ValueError):
                dlc.validate_sentinel(bytes(damaged), bytes(range(16)))
        with self.assertRaises(ValueError):
            dlc.validate_sentinel(FIXTURE, bytes(16))
        with self.assertRaises(ValueError):
            dlc.validate_sentinel(FIXTURE[:-1], bytes(range(16)))

    def test_wrong_package_does_not_touch_hdd(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            pkg = root / "wrong.pkg"
            pkg.write_bytes(b"\x7fPKG" + bytes(124))
            with self.assertRaisesRegex(ValueError, "not the supported US"):
                dlc.import_dlc(pkg, root / "missing.rap", root / "hdd")
            self.assertFalse((root / "hdd").exists())


if __name__ == "__main__":
    unittest.main()
