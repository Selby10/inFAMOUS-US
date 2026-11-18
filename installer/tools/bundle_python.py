"""Rebuild the Windows player runtime from hash-pinned CPython and wheel archives."""
import hashlib
import io
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from urllib.request import urlopen
from zipfile import ZIP_DEFLATED, ZipFile, ZipInfo

VERSION = "3.13.17"
PYTHON_HASH = "9e35319bffedab8b36ad44d8f377cb4f1733bcec257a0b6d04d94f01486ddb38"
WHEELS = {
    "capstone-5.0.9-py3-none-win_amd64.whl": "732cedbbb56d42e723f14d7af6387f1454194a820b4b96b56d1e53f865ef85d0",
    "colorama-0.4.6-py2.py3-none-any.whl": "4f1d9991f5acc0ca119f9d443620b77f9d6b33703e51011c16baf57afb285fc6",
    "construct-2.10.70-py3-none-any.whl": "c80be81ef595a1a821ec69dc16099550ed22197615f4320b57cc9ce2a672cb30",
    "numpy-2.5.4-cp313-cp313-win_amd64.whl": "8b4d2fd2d34e5f8c9235ee787de5631a37a28402b15cb80814df973d2be54129",
    "pillow-13.0.0-cp313-cp313-win_amd64.whl": "9dda0788a9932776a75d8bcc020076edc308ce2508b51f6739a45e3158b3fc42",
    "pycryptodome-4.4.0-cp39-abi3-win_amd64.whl": "c5571df4f386ff4cdc0d5d0d040af9c0bdfc59ad587bf224063d873e07acbe61",
    "tabulate-0.10.0-py3-none-any.whl": "f0b0622e567335c8fabaaa659f1b33bcb6ddfe2e496071b743aa113f8774f2d3",
    "tqdm-4.70.1-py3-none-any.whl": "c293e525e6fef9c20e8728fd4612df02a0aa31bb5fe91ecd93e123b1b7bffa73",
}


def add_archive(bundle, url, digest, prefix):
    with urlopen(url, timeout=90) as response:
        data = response.read()
    if hashlib.sha256(data).hexdigest() != digest:
        raise ValueError(f"Unexpected SHA-256 for {url}")
    with ZipFile(io.BytesIO(data)) as source:
        for entry in source.infolist():
            if entry.is_dir():
                continue
            name = entry.filename
            if ".data/" in name:
                raise ValueError(f"Wheel needs an install scheme for {name}")
            contents = source.read(entry)
            if name == "python313._pth":
                # Isolated Python omits the script directory. Several pipeline tools
                # import siblings, including processes spawned by the SPU lifter.
                contents = b"python313.zip\n.\npackages\n../../ps3recomp/tools\n../../infamous-recomp/tools\n"
            info = ZipInfo(prefix + name)
            bundle.writestr(info, contents, compress_type=ZIP_DEFLATED)


def main():
    dest = Path(__file__).resolve().parents[1] / "runtime"
    dest.mkdir(exist_ok=True)
    with TemporaryDirectory(dir=dest) as tmp:
        archive = Path(tmp) / "python-win64.zip"
        with ZipFile(archive, "w") as bundle:
            add_archive(bundle, f"https://www.python.org/ftp/python/{VERSION}/python-{VERSION}-embed-amd64.zip", PYTHON_HASH, "")
            for wheel, digest in WHEELS.items():
                package, version, *_ = wheel.split("-")
                with urlopen(f"https://pypi.org/pypi/{package}/{version}/json", timeout=30) as response:
                    files = json.load(response)["urls"]
                url = next(f["url"] for f in files if f["filename"] == wheel)
                add_archive(bundle, url, digest, "packages/")
        digest = hashlib.sha256(archive.read_bytes()).hexdigest()
        archive.replace(dest / archive.name)
        (dest / "python-win64.sha256").write_text(digest + "\n", encoding="ascii")
        print(f"{archive.name}  {digest}")


if __name__ == "__main__":
    main()
