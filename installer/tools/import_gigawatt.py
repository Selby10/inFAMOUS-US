"""Import the user's US Gigawatt Blades package/license into a private HDD tree.

Requires pycryptodome (already a ps3recomp requirement). Package extraction uses
the existing toolkit. RAP conversion follows the documented NPDRM algorithm;
reference: RPCS3/rpcs3, Crypto/key_vault.cpp, rap_to_rif.
Only the DLC sentinel and its license sidecar are installed, preserving any
existing title metadata, updates and saves. No game bytes or licenses ship here.
"""
import argparse
from pathlib import Path
import struct
import subprocess
import sys
import tempfile

from Crypto.Cipher import AES
from Crypto.Hash import CMAC

CONTENT_ID = "UP9000-BCUS98119_00-SMLAUNCHPROMO000"
SENTINEL = "super_melee_sentinel.edat"


def rap_to_rif(rap):
    if len(rap) != 16:
        raise ValueError("RAP must contain exactly 16 bytes")
    key = bytearray(AES.new(bytes.fromhex("869f7745c13fd890ccf29188e3cc3edf"),
                            AES.MODE_ECB).decrypt(rap))
    order = bytes.fromhex("0c030604010b0f08020700050a0e0d09")
    e1 = bytes.fromhex("a93e1fd67c55a329b75fdda62a95c7a5")
    e2 = bytes.fromhex("67d45da3296d006a4e7c537bf5538c74")
    for _ in range(5):
        for p in order:
            key[p] ^= e1[p]
        for i in range(15, 0, -1):
            key[order[i]] ^= key[order[i - 1]]
        carry = 0
        for p in order:
            value = (key[p] - carry) & 255
            if carry != 1 or value != 255:
                carry = int(value < e2[p])
            key[p] = (value - e2[p]) & 255
    return bytes(key)


def validate_sentinel(data, rif):
    """Authenticate the known US single-block EDAT before touching installed data."""
    if len(data) != 320 or data[:4] != b"NPD\0":
        raise ValueError("Unexpected Gigawatt sentinel format")
    if data[16:64].rstrip(b"\0") != CONTENT_ID.encode():
        raise ValueError("Sentinel content ID does not match the US DLC")
    if struct.unpack_from(">II", data, 4) != (2, 2):
        raise ValueError("Unsupported EDAT version/license")
    if struct.unpack_from(">IIQ", data, 128) != (12, 16384, 30):
        raise ValueError("Unsupported sentinel flags/block size/payload size")
    block_key = AES.new(rif, AES.MODE_ECB).encrypt(data[96:108] + bytes(4))
    key = AES.new(bytes.fromhex("be959ca8308defa2e5e180c63712a9ae"),
                  AES.MODE_ECB).decrypt(block_key)
    mac = CMAC.new(key, ciphermod=AES)
    mac.update(data[272:304])
    try:
        mac.verify(data[256:272])
    except ValueError:
        raise ValueError("DLC authentication failed: wrong RAP or corrupt EDAT") from None
    # Exercise the same payload decrypt as the runtime without writing plaintext.
    return AES.new(key, AES.MODE_CBC, data[64:80]).decrypt(data[272:304])[:30]


def import_dlc(pkg, rap, hdd_root):
    with pkg.open("rb") as f:
        header = f.read(128)
    if len(header) != 128 or header[:4] != b"\x7fPKG":
        raise ValueError("Input is not a PS3 PKG")
    if header[48:96].rstrip(b"\0") != CONTENT_ID.encode():
        raise ValueError("Package is not the supported US Gigawatt Blades DLC")
    if rap.stem != CONTENT_ID:
        raise ValueError("RAP filename must match the package content ID")
    rif = rap_to_rif(rap.read_bytes())
    extractor = Path(__file__).resolve().parents[2] / "ps3recomp/tools/pkg_extract.py"
    with tempfile.TemporaryDirectory(prefix="infamous-dlc-") as staging:
        subprocess.run([sys.executable, str(extractor), str(pkg), staging,
                        "--only", SENTINEL], check=True, capture_output=True)
        data = (Path(staging) / "USRDIR" / SENTINEL).read_bytes()
        validate_sentinel(data, rif)
    target = hdd_root.resolve() / "game/BCUS98119/USRDIR" / SENTINEL
    target.parent.mkdir(parents=True, exist_ok=True)
    for path, content in [(target, data), (Path(str(target) + ".rifkey"), rif)]:
        temporary = path.with_name(path.name + ".tmp")
        temporary.write_bytes(content)
        temporary.replace(path)
    # A previously decrypted cache may belong to a different license/content.
    Path(str(target) + ".dec").unlink(missing_ok=True)
    print("US Gigawatt Blades installed; EDAT block authentication passed.")
    print(f"Launch with PS3_HDD0_ROOT={hdd_root.resolve()}")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pkg", required=True, type=Path)
    parser.add_argument("--rap", required=True, type=Path)
    parser.add_argument("--hdd-root", required=True, type=Path)
    args = parser.parse_args()
    try:
        import_dlc(args.pkg, args.rap, args.hdd_root)
    except (ValueError, OSError, subprocess.CalledProcessError) as error:
        parser.exit(1, f"DLC import failed: {error}\n")


if __name__ == "__main__":
    main()
