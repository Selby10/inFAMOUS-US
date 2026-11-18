//! Private CPython and its wheels, shipped together so Setup and rebuilds work offline.

use crate::util::{self, Ctx, Result};
use crate::{bail, hash};
use std::fs;
use std::path::{Path, PathBuf};
use std::process::Command;

pub const ARCHIVE: &str = "installer/runtime/python-win64.zip";
pub const CHECKSUM: &str = "installer/runtime/python-win64.sha256";

fn checksum(root: &Path) -> Result<String> {
    let path = root.join(CHECKSUM);
    let text = fs::read_to_string(&path).ctx(|| format!("reading {}", path.display()))?;
    let digest = text.trim();
    if digest.len() != 64 || !digest.bytes().all(|b| b.is_ascii_hexdigit()) {
        bail!("invalid Python runtime checksum in {}", path.display());
    }
    Ok(digest.to_ascii_lowercase())
}

fn verify(root: &Path, expected: &str) -> Result<()> {
    let path = root.join(ARCHIVE);
    let actual = hash::sha256_file(&path).ctx(|| format!("reading {}", path.display()))?;
    if actual != expected {
        bail!(
            "Python runtime checksum mismatch in {}. Restore the packaged runtime and retry.",
            path.display()
        );
    }
    Ok(())
}

pub fn verify_for_package(root: &Path) -> Result<()> {
    verify(root, &checksum(root)?)
}

pub fn prepare(root: &Path, minimum: (u32, u32)) -> Result<PathBuf> {
    let digest = checksum(root)?;
    let dest = root
        .join(".installer")
        .join(format!("python-{}", &digest[..16]));
    fs::create_dir_all(&dest)?;
    // All installer processes take this lock. An interrupted extraction leaves the
    // ready marker absent, and the next run can finish from the same local archive.
    let lock = fs::OpenOptions::new()
        .create(true)
        .truncate(false)
        .read(true)
        .write(true)
        .open(dest.join("unpack.lock"))?;
    lock.lock()?;
    let exe = dest.join("python.exe");
    let ready = dest.join("runtime.ready");
    let probe = || {
        const CHECK: &str = r#"
import sys
import numpy, PIL.Image, capstone, construct, tabulate, tqdm
from Crypto.Cipher import AES
sys.exit(0 if sys.version_info[:2] >= tuple(map(int, sys.argv[1:])) else 1)
"#;
        util::capture(Command::new(&exe).args([
            "-B",
            "-c",
            CHECK,
            &minimum.0.to_string(),
            &minimum.1.to_string(),
        ]))
        .is_some()
    };
    if ready.is_file() {
        if probe() {
            return Ok(exe);
        }
        fs::remove_file(&ready)?;
    }
    verify(root, &digest)?;
    let tar = PathBuf::from(std::env::var_os("SystemRoot").ctx(|| "Windows directory missing")?)
        .join("System32/tar.exe");
    util::run(
        Command::new(tar)
            .arg("-xf")
            .arg(root.join(ARCHIVE))
            .arg("-C")
            .arg(&dest),
    )?;
    if !probe() {
        bail!(
            "Python {}.{}+ and its bundled libraries could not be loaded from {}",
            minimum.0,
            minimum.1,
            dest.display()
        );
    }
    fs::write(ready, [])?;
    Ok(exe)
}
