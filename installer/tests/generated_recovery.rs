use infamous_installer::{
    diff, hash,
    lock::{self, Entry, Lock, State},
    manifest::Manifest,
    pipeline,
};
use std::fs;
use std::path::PathBuf;

const BASE: &[u8] = b"clean lift\n";
const FINAL: &[u8] = b"current patch\n";
const STALE: &[u8] = b"previous patch\n";
const BAD_GENERATION: &[u8] = b"unexpected lift output\n";
const PPU: &str = "generated/ppu_recomp_001.cpp";
const OTHER: &str = "generated/000_other.cpp";
const STRAY: &str = "generated/stray.cpp";
const DELETED: &str = "generated/deleted.cpp";
const OPTIONAL: &str = "generated/optional.cpp";
const TITLE_ID: &str = "BCUS98119";
const EBOOT: &str = "fixture-eboot";

struct Workspace(PathBuf);

impl Workspace {
    fn new() -> Self {
        let path = std::env::temp_dir().join(format!(
            "infamous-generated-{}-{}",
            std::process::id(),
            std::time::SystemTime::now()
                .duration_since(std::time::UNIX_EPOCH)
                .unwrap()
                .as_nanos()
        ));
        fs::create_dir(&path).unwrap();
        Self(path)
    }

    fn write(&self, rel: &str, bytes: &[u8]) {
        let path = self.0.join(rel);
        fs::create_dir_all(path.parent().unwrap()).unwrap();
        fs::write(path, bytes).unwrap();
    }

    fn read(&self, rel: &str) -> Vec<u8> {
        fs::read(self.0.join(rel)).unwrap()
    }

    fn cache_lift(&self) {
        State {
            eboot: Some(EBOOT.into()),
            done: vec!["lift".into()],
            applied: true,
        }
        .save(&self.0.join(".installer/state"))
        .unwrap();
    }

    fn install(&self, manifest: &Manifest, lock: &Lock, regen: bool) -> Result<(), String> {
        let installer = self.0.join("installer");
        let region = lock::region_dir(&installer, TITLE_ID);
        pipeline::generate_and_apply_patches(
            manifest,
            &self.0,
            &pipeline::workspace_vars(manifest, &self.0, &installer),
            EBOOT,
            pipeline::PatchSet {
                lock,
                title_id: TITLE_ID,
                installer_dir: &installer,
                region: region.as_deref(),
            },
            regen,
        )
        .map_err(|e| e.to_string())
    }
}

impl Drop for Workspace {
    fn drop(&mut self) {
        let _ = fs::remove_dir_all(&self.0);
    }
}

fn manifest(steps: &str) -> Manifest {
    Manifest::parse(&format!(
        r#"
        [game]
        title = "Synthetic fixture"
        [layout]
        port = "generated"
        toolkit = "tools"
        [python]
        [generated]
        include = ["generated/*.cpp"]
        [build]
        exe = "game.exe"
        [launch]
        name = "Play.cmd"
        {steps}
        "#,
    ))
    .unwrap()
}

const LIFT: &str = r#"
    [[step]]
    id = "lift"
    builtin = "copy"
    args = ["{root}/fixture-clean.cpp", "{port}/ppu_recomp_001.cpp"]
"#;

fn entry(path: &str) -> Entry {
    Entry {
        path: path.into(),
        base: Some(hash::sha256_hex(BASE)),
        fin: Some(hash::sha256_hex(FINAL)),
    }
}

#[test]
fn stale_patch_input_leaves_other_generated_files_untouched() {
    let ws = Workspace::new();
    ws.write(OTHER, BASE);
    ws.write(PPU, STALE);
    ws.write(STRAY, b"stray");
    ws.write(DELETED, b"delete after preflight");
    let (patch, _) = diff::make_patch(BASE, FINAL, OTHER, 3);
    ws.write(&format!("installer/patches/{OTHER}.patch"), &patch);
    let lock = Lock {
        eboot: None,
        files: vec![
            entry(OTHER),
            Entry {
                path: DELETED.into(),
                base: Some(hash::sha256_hex(b"delete after preflight")),
                fin: None,
            },
            entry(PPU),
        ],
    };
    ws.write("fixture-clean.cpp", BAD_GENERATION);
    ws.cache_lift();
    let err = ws.install(&manifest(LIFT), &lock, false).unwrap_err();

    for part in [
        TITLE_ID,
        PPU,
        &hash::sha256_hex(BASE),
        &hash::sha256_hex(FINAL),
        &hash::sha256_hex(BAD_GENERATION),
    ] {
        assert!(err.contains(part), "{err}");
    }
    assert_eq!(ws.read(OTHER), BASE);
    assert_eq!(ws.read(PPU), BAD_GENERATION);
    assert_eq!(ws.read(STRAY), b"stray");
    assert_eq!(ws.read(DELETED), b"delete after preflight");
    assert!(!State::load(&ws.0.join(".installer/state")).applied);
}

#[test]
fn cached_previous_us_patch_is_regenerated_and_patched() {
    let ws = Workspace::new();
    ws.write(PPU, STALE);
    ws.write(OTHER, BASE);
    ws.write(DELETED, b"old deletion");
    ws.write(STRAY, b"old stray");
    ws.write("fixture-clean.cpp", BASE);
    ws.write(".installer/cached-python/sentinel", b"keep runtime");
    ws.write("HDD/save", b"keep saves");
    ws.write("infamous.ini", b"keep settings");
    ws.cache_lift();
    let lock = Lock {
        eboot: Some(EBOOT.into()),
        files: vec![
            entry(PPU),
            entry(OTHER),
            Entry {
                path: DELETED.into(),
                base: Some(hash::sha256_hex(b"old deletion")),
                fin: None,
            },
        ],
    };
    let region = ws.0.join("installer/regions").join(TITLE_ID);
    let lock_path = region.join("generated.lock");
    lock.save(&lock_path).unwrap();
    let (patch, _) = diff::make_patch(BASE, FINAL, PPU, 3);
    ws.write(
        &format!("installer/regions/{TITLE_ID}/patches/{PPU}.patch"),
        &patch,
    );
    ws.write(&format!("installer/patches/{PPU}.patch"), b"wrong region");
    let (patch, _) = diff::make_patch(BASE, FINAL, OTHER, 3);
    ws.write(&format!("installer/patches/{OTHER}.patch"), &patch);

    ws.install(
        &manifest(LIFT),
        &Lock::load(&lock_path).unwrap().unwrap(),
        false,
    )
    .unwrap();

    assert_eq!(ws.read(PPU), FINAL);
    assert_eq!(ws.read(OTHER), FINAL);
    assert!(!ws.0.join(DELETED).exists());
    assert!(!ws.0.join(STRAY).exists());
    assert_eq!(
        ws.read(".installer/cached-python/sentinel"),
        b"keep runtime"
    );
    assert_eq!(ws.read("HDD/save"), b"keep saves");
    assert_eq!(ws.read("infamous.ini"), b"keep settings");
}

#[test]
fn clean_and_final_files_resume_without_rerunning_cached_step() {
    let ws = Workspace::new();
    ws.write(PPU, FINAL);
    ws.write(OTHER, BASE);
    // There is deliberately no fixture-clean.cpp, so rerunning lift would fail.
    ws.cache_lift();
    let lock = Lock {
        eboot: None,
        files: vec![entry(OTHER), entry(PPU), entry(OPTIONAL)],
    };
    let (patch, _) = diff::make_patch(BASE, FINAL, OTHER, 3);
    ws.write(&format!("installer/patches/{OTHER}.patch"), &patch);

    let steps = format!(
        r#"
        {LIFT}
        [[step]]
        id = "optional"
        builtin = "copy"
        args = ["{{root}}/missing-asset", "{{root}}/{OPTIONAL}"]
        optional = true
        outputs = ["{OPTIONAL}"]
        "#
    );
    ws.install(&manifest(&steps), &lock, false).unwrap();

    assert_eq!(ws.read(PPU), FINAL);
    assert_eq!(ws.read(OTHER), FINAL);
    assert!(!ws.0.join(OPTIONAL).exists());
}

#[test]
fn bad_patch_payload_fails_without_rerunning_cached_step() {
    let ws = Workspace::new();
    ws.write(PPU, BASE);
    ws.write(&format!("installer/patches/{PPU}.patch"), b"bad patch");
    ws.cache_lift();
    let lock = Lock {
        eboot: None,
        files: vec![entry(PPU)],
    };

    let err = ws.install(&manifest(LIFT), &lock, false).unwrap_err();

    assert!(err.contains("ppu_recomp_001.cpp.patch"), "{err}");
    assert_eq!(ws.read(PPU), BASE);
    assert!(State::load(&ws.0.join(".installer/state")).applied);
}

#[test]
fn fresh_or_explicit_generation_with_a_mismatch_is_not_repeated() {
    let manifest = manifest(&format!(
        r#"
        {LIFT}
        [[step]]
        id = "advance"
        builtin = "copy"
        args = ["{{root}}/fixture-next.cpp", "{{root}}/fixture-clean.cpp"]
        "#
    ));
    let lock = Lock {
        eboot: None,
        files: vec![entry(PPU)],
    };
    for regen in [false, true] {
        let ws = Workspace::new();
        if regen {
            ws.cache_lift();
        }
        ws.write("fixture-clean.cpp", BAD_GENERATION);
        ws.write("fixture-next.cpp", BASE);
        let (patch, _) = diff::make_patch(BASE, FINAL, PPU, 3);
        ws.write(&format!("installer/patches/{PPU}.patch"), &patch);

        let err = ws.install(&manifest, &lock, regen).unwrap_err();

        assert!(err.contains(&hash::sha256_hex(BAD_GENERATION)), "{err}");
        // A second pipeline run would see BASE and succeed.
        assert_eq!(ws.read(PPU), BAD_GENERATION);
    }
}
