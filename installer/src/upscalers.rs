//! Windows SDK provisioning for the local game build.

use crate::util::{self, Ctx, Result};
use crate::{bail, hash, update};
use std::fs;
use std::path::{Path, PathBuf};

const CACHE: &str = ".installer/upscalers";

struct Sdk {
    name: &'static str,
    directory: &'static str,
    repository: &'static str,
    revision: &'static str,
    files: &'static str,
    license: (&'static str, &'static str),
    cmake_var: &'static str,
}

// DLSS 310.9.16 and AMD FSR SDK 2.4.0. The manifests list only the headers,
// library, signed runtimes and notices needed by the D3D12 integration.
const DLSS: Sdk = Sdk {
    name: "DLSS",
    directory: "dlss-sdk",
    repository: "NVIDIA/DLSS",
    revision: "9981c6350dc58c0ce21f024fd65e31e851c31689",
    files: include_str!("upscalers/dlss.sha256"),
    license: ("LICENSE.txt", "NVIDIA-DLSS.txt"),
    cmake_var: "PS3RECOMP_DLSS_SDK",
};
const FSR: Sdk = Sdk {
    name: "FSR",
    directory: "fsr-sdk",
    repository: "GPUOpen-LibrariesAndSDKs/FidelityFX-SDK",
    revision: "7539ae8b6568ad29ed78a753404351daf3b5cca7",
    files: include_str!("upscalers/fsr.sha256"),
    license: ("Kits/FidelityFX/docs/license.md", "AMD-FidelityFX.md"),
    cmake_var: "PS3RECOMP_FSR_SDK",
};

impl Sdk {
    fn cache_path(&self) -> String {
        format!("{CACHE}/{}-{}", self.directory, self.revision)
    }

    fn entries(&self) -> impl Iterator<Item = (&str, &str)> {
        self.files
            .lines()
            .map(|line| line.split_once(' ').expect("SDK file manifest"))
    }

    fn prepare(&self, root: &Path) -> Result<PathBuf> {
        let cache = root.join(self.cache_path());
        fs::create_dir_all(&cache)?;
        // Provision pinned files in place one at a time. A retry reuses verified
        // files from the Setup payload or any earlier installation attempt.
        let lock = fs::OpenOptions::new()
            .create(true)
            .truncate(false)
            .read(true)
            .write(true)
            .open(cache.join("download.lock"))?;
        lock.lock()?;
        for (digest, relative) in self.entries() {
            let destination = cache.join(relative);
            if verified(&destination, digest) {
                continue;
            }
            util::info(&format!("Downloading {} {}", self.name, relative));
            util::ensure_parent(&destination)?;
            let temporary = destination.with_extension("download");
            let url = format!(
                "https://raw.githubusercontent.com/{}/{}/{}",
                self.repository, self.revision, relative
            );
            util::run(
                update::curl_program()
                    .args([
                        "--fail",
                        "--silent",
                        "--show-error",
                        "--location",
                        "--retry",
                        "2",
                        "--retry-max-time",
                        "120",
                        "--connect-timeout",
                        "10",
                        "--max-time",
                        "120",
                    ])
                    .arg("--output")
                    .arg(&temporary)
                    .arg(url),
            )?;
            if !verified(&temporary, digest) {
                fs::remove_file(&temporary)?;
                bail!("checksum mismatch for {} {}", self.name, relative);
            }
            fs::rename(&temporary, &destination)?;
        }
        Ok(cache)
    }

    fn stage_runtime(&self, sdk: &Path, output: &Path) -> Result<()> {
        let lock = fs::OpenOptions::new()
            .create(true)
            .truncate(false)
            .read(true)
            .write(true)
            .open(output.join(format!("{}.lock", self.directory)))?;
        lock.lock()?;
        for (digest, relative) in self.entries() {
            let source = sdk.join(relative);
            let destination = if relative.ends_with(".dll") {
                output.join(source.file_name().expect("SDK runtime filename"))
            } else if relative == self.license.0 {
                output.join("licenses").join(self.license.1)
            } else {
                continue;
            };
            if !verified(&destination, digest) {
                let temporary = destination.with_extension("staging");
                util::copy_file(&source, &temporary)?;
                if !verified(&temporary, digest) {
                    bail!("checksum mismatch staging {}", source.display());
                }
                fs::rename(&temporary, &destination)
                    .ctx(|| format!("staging {}", destination.display()))?;
            }
        }
        Ok(())
    }
}

fn verified(file: &Path, digest: &str) -> bool {
    hash::sha256_file(file).is_ok_and(|actual| actual == digest)
}

/// Let an existing installation pick up the feature after updating its installer
/// binary, and let a later update retry optional downloads that were interrupted.
pub fn needs_provisioning(root: &Path) -> bool {
    cfg!(windows)
        && (fs::read_to_string(root.join(CACHE).join("build.ready"))
            .ok()
            .as_deref()
            != Some(&ready_stamp())
            || [&DLSS, &FSR].iter().any(|sdk| {
                let cache = root.join(sdk.cache_path());
                sdk.entries().any(|(_, name)| !cache.join(name).is_file())
            }))
}

fn ready_stamp() -> String {
    format!("{}\n{}\n", DLSS.revision, FSR.revision)
}

pub fn record_build(root: &Path, ready: bool) -> Result<()> {
    let path = root.join(CACHE).join("build.ready");
    if ready {
        util::write_file(&path, ready_stamp().as_bytes())
    } else {
        match fs::remove_file(path) {
            Ok(()) => Ok(()),
            Err(e) if e.kind() == std::io::ErrorKind::NotFound => Ok(()),
            Err(e) => Err(e.into()),
        }
    }
}

pub fn configure_args(root: &Path, output: &Path) -> Result<(Vec<String>, bool)> {
    fs::create_dir_all(output)?;
    let mut args = Vec::new();
    let mut ready = true;
    for sdk in [&DLSS, &FSR] {
        let directory = match sdk.prepare(root).and_then(|path| {
            sdk.stage_runtime(&path, output)?;
            Ok(path)
        }) {
            Ok(path) => {
                util::info(&format!("{} runtime ready", sdk.name));
                path
            }
            Err(e) => {
                ready = false;
                util::warn(&format!(
                    "{} setup could not finish: {e}. The game can still be built. Run Setup or Update again to retry.",
                    sdk.name
                ));
                root.join(sdk.directory)
            }
        };
        args.push(format!(
            "-D{}:PATH={}",
            sdk.cmake_var,
            util::path_str(&directory).replace('\\', "/")
        ));
    }
    Ok((args, ready))
}

/// Package AMD's permitted redistributables into the private SDK cache.
/// NVIDIA's files are fetched directly by the installer at build time.
pub fn bundled_fsr(root: &Path) -> Result<Vec<(String, PathBuf)>> {
    let source = FSR.prepare(root)?;
    let target = FSR.cache_path();
    Ok(FSR
        .entries()
        .map(|(_, file)| (format!("{target}/{file}"), source.join(file)))
        .collect())
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn stages_and_repairs_only_verified_runtime_files() {
        let root = std::env::temp_dir().join(format!("infamous-sdk-stage-{}", std::process::id()));
        let output = root.join("game");
        let cache = root.join("sdk");
        fs::create_dir_all(&output).unwrap();
        let sdk = Sdk {
            files: "248d6a61d20638b8e5c026930c3e6039a33ce45964ff2167f6ecedd419db06c1 bin/test.dll",
            ..DLSS
        };
        let source = cache.join("bin/test.dll");
        let target = output.join("test.dll");
        util::write_file(
            &source,
            b"abcdbcdecdefdefgefghfghighijhijkijkljklmklmnlmnomnopnopq",
        )
        .unwrap();
        fs::write(&target, b"stale").unwrap();
        sdk.stage_runtime(&cache, &output).unwrap();
        let contents = fs::read(&target).unwrap();
        assert_eq!(contents, fs::read(&source).unwrap());

        fs::write(&target, b"stale").unwrap();
        fs::write(&source, b"bad download").unwrap();
        assert!(sdk.stage_runtime(&cache, &output).is_err());
        assert_eq!(fs::read(target).unwrap(), b"stale");
        fs::remove_dir_all(root).unwrap();
    }
}
