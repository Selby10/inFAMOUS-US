#![cfg(windows)]

use infamous_installer::{payload::Payload, python_bundle};
use std::fs;
use std::path::PathBuf;
use std::process::Command;

#[test]
#[ignore = "run after packaging with INFAMOUS_TEST_SETUP pointing to the built Setup executable"]
fn packaged_python_runs_offline() {
    let setup = PathBuf::from(std::env::var_os("INFAMOUS_TEST_SETUP").unwrap());
    let root = std::env::temp_dir().join(format!("inFamous package test {}", std::process::id()));
    fs::create_dir(&root).unwrap();
    let payload = Payload::open(&setup).unwrap().expect("Setup payload");
    payload.extract(&root, &mut |_, _| {}).unwrap();

    // Exercise the installed CLI's selection and first-run extraction with only Windows on PATH.
    let system = PathBuf::from(std::env::var_os("SystemRoot").unwrap()).join("System32");
    let doctor = Command::new(root.join("installer/infamous-installer.exe"))
        .arg("doctor")
        .arg("--root")
        .arg(&root)
        .env("PATH", &system)
        .env("PIP_NO_INDEX", "1")
        .output()
        .unwrap();
    let output = String::from_utf8_lossy(&doctor.stdout);
    let python = python_bundle::prepare(&root, (3, 10)).unwrap();
    // Compiler availability is independent of the Python check.
    let reported = output.replace('\\', "/");
    assert!(
        reported.lines().any(|line| line.contains("OK  Python")
            && line.contains(&python.to_string_lossy().replace('\\', "/"))),
        "{output}"
    );

    let status = Command::new(&python)
        .env("PATH", &system).env("PYTHONPATH", "Z:/unused")
        .args(["-B", "-c", "import sys; assert sys.flags.isolated; import numpy, PIL.Image, capstone, construct, tabulate, tqdm, wrap_spu_elf, psarc, kbm_glyphs_dxt; from Crypto.Cipher import AES"])
        .status().unwrap();
    assert!(status.success());
    assert!(!root.join(".installer/venv").exists());

    // Simulate a damaged cache, then verify a rebuild repairs it from the payload.
    fs::remove_file(
        python
            .parent()
            .unwrap()
            .join("packages/Crypto/Cipher/AES.py"),
    )
    .unwrap();
    assert_eq!(python_bundle::prepare(&root, (3, 10)).unwrap(), python);

    fs::write(root.join(python_bundle::CHECKSUM), "0".repeat(64)).unwrap();
    assert!(python_bundle::prepare(&root, (3, 10))
        .unwrap_err()
        .to_string()
        .contains("checksum mismatch"));
    fs::remove_dir_all(root).unwrap();
}
