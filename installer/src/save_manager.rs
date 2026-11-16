//! Local, hash-verified snapshots. The launcher holds GameLock during operations.
use crate::hash::sha256_file;
use std::{
    fs, io,
    path::{Component, Path, PathBuf},
    time::{SystemTime, UNIX_EPOCH},
};

pub struct Manager {
    pub saves: PathBuf,
    pub settings: PathBuf,
    pub backups: PathBuf,
}
#[derive(Clone)]
pub struct Entry {
    pub name: String,
    pub modified: String,
}
#[derive(Clone, Copy)]
pub enum Scope {
    Saves,
    Settings,
    Both,
}
impl Scope {
    fn saves(self) -> bool {
        matches!(self, Self::Saves | Self::Both)
    }
    fn settings(self) -> bool {
        matches!(self, Self::Settings | Self::Both)
    }
}
fn error(s: impl Into<String>) -> io::Error {
    io::Error::other(s.into())
}
fn id() -> String {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .unwrap()
        .as_nanos()
        .to_string()
}
fn linked(path: &Path) -> io::Result<bool> {
    let m = fs::symlink_metadata(path)?;
    #[cfg(windows)]
    {
        use std::os::windows::fs::MetadataExt;
        if m.file_attributes() & 0x400 != 0 {
            return Ok(true);
        }
    }
    Ok(m.file_type().is_symlink())
}
fn safe_relative(s: &str) -> bool {
    !s.is_empty()
        && !s.contains(['\n', '\r', '\t'])
        && Path::new(s)
            .components()
            .all(|c| matches!(c, Component::Normal(_)))
}
fn settings_file(path: &Path) -> bool {
    path == Path::new("settings.ini") || path == Path::new("controls.ini")
}
fn save_name(s: &str) -> bool {
    (s.starts_with("BCUS98119") || s.starts_with("BCES00220"))
        && safe_relative(s)
        && !s.contains(['/', '\\'])
}
fn files(root: &Path, relative: &Path, out: &mut Vec<PathBuf>) -> io::Result<()> {
    let path = root.join(relative);
    if linked(&path)? {
        return Err(error("Linked/reparse files are not supported in snapshots"));
    }
    for item in fs::read_dir(&path)? {
        let p = item?.path();
        if linked(&p)? {
            return Err(error("Linked/reparse files are not supported in snapshots"));
        }
        let rel = p
            .strip_prefix(root)
            .map_err(|_| error("Invalid snapshot path"))?
            .to_path_buf();
        if !safe_relative(rel.to_str().ok_or_else(|| error("Unsupported filename"))?) {
            return Err(error("Invalid snapshot filename"));
        }
        if p.is_dir() {
            files(root, &rel, out)?;
        } else if p.is_file() {
            out.push(rel);
        }
    }
    Ok(())
}
fn copy_file(from: &Path, to: &Path) -> io::Result<()> {
    fs::create_dir_all(to.parent().ok_or_else(|| error("No destination folder"))?)?;
    fs::copy(from, to)?;
    if sha256_file(from)? != sha256_file(to)? {
        return Err(error("Copied file failed hash verification"));
    }
    Ok(())
}
fn stamp(time: SystemTime) -> String {
    let seconds = time
        .duration_since(UNIX_EPOCH)
        .unwrap_or_default()
        .as_secs();
    let z = (seconds / 86400) as i64 + 719468;
    let era = z / 146097;
    let doe = z - era * 146097;
    let yoe = (doe - doe / 1460 + doe / 36524 - doe / 146096) / 365;
    let mut y = yoe + era * 400;
    let doy = doe - (365 * yoe + yoe / 4 - yoe / 100);
    let mp = (5 * doy + 2) / 153;
    let d = doy - (153 * mp + 2) / 5 + 1;
    let m = mp + if mp < 10 { 3 } else { -9 };
    y += i64::from(m <= 2);
    format!(
        "{y:04}-{m:02}-{d:02} {:02}:{:02} UTC",
        seconds / 3600 % 24,
        seconds / 60 % 60
    )
}
impl Manager {
    pub fn list_saves(&self) -> io::Result<Vec<Entry>> {
        if !self.saves.exists() {
            return Ok(Vec::new());
        }
        let mut entries = Vec::new();
        for entry in fs::read_dir(&self.saves)? {
            let entry = entry?;
            let name = entry.file_name().to_string_lossy().into_owned();
            if save_name(&name) && entry.path().is_dir() && !linked(&entry.path())? {
                let mut save_files = Vec::new();
                files(&entry.path(), Path::new(""), &mut save_files)?;
                let mut modified = entry.metadata()?.modified()?;
                for file in save_files {
                    modified = modified.max(fs::metadata(entry.path().join(file))?.modified()?);
                }
                entries.push(Entry {
                    name,
                    modified: stamp(modified),
                });
            }
        }
        entries.sort_by(|a, b| a.name.cmp(&b.name));
        Ok(entries)
    }
    pub fn list_backups(&self) -> io::Result<Vec<Entry>> {
        if !self.backups.exists() {
            return Ok(Vec::new());
        }
        let mut entries = Vec::new();
        for entry in fs::read_dir(&self.backups)? {
            let entry = entry?;
            let name = entry.file_name().to_string_lossy().into_owned();
            if safe_relative(&name)
                && entry.path().join("snapshot.txt").is_file()
                && !linked(&entry.path())?
            {
                entries.push(Entry {
                    name,
                    modified: stamp(entry.metadata()?.modified()?),
                });
            }
        }
        entries.sort_by(|a, b| b.name.cmp(&a.name));
        Ok(entries)
    }
    pub fn backup(&self, scope: Scope) -> io::Result<PathBuf> {
        self.snapshot(scope, "backup")
    }
    fn snapshot(&self, scope: Scope, prefix: &str) -> io::Result<PathBuf> {
        fs::create_dir_all(&self.backups)?;
        let dest = self.backups.join(format!("{prefix}-{}", id()));
        fs::create_dir(&dest)?;
        let mut paths = Vec::new();
        if scope.saves() {
            for entry in self.list_saves()? {
                let mut children = Vec::new();
                files(&self.saves, Path::new(&entry.name), &mut children)?;
                for rel in children {
                    let target = Path::new("saves").join(&rel);
                    copy_file(&self.saves.join(rel), &dest.join(&target))?;
                    paths.push(target);
                }
            }
        }
        if scope.settings() {
            for name in ["settings.ini", "controls.ini"] {
                let path = self.settings.with_file_name(name);
                if path.exists() {
                    if linked(&path)? {
                        return Err(error("Linked settings file is not supported"));
                    }
                    copy_file(&path, &dest.join(name))?;
                    paths.push(PathBuf::from(name));
                }
            }
        }
        if paths.is_empty() {
            return Err(error("No saves or settings exist to back up"));
        }
        let mut manifest = String::from("INFAMOUS-SNAPSHOT-1\n");
        for rel in paths {
            manifest.push_str(&format!(
                "{}\t{}\n",
                sha256_file(&dest.join(&rel))?,
                rel.to_string_lossy()
            ));
        }
        // Publishing the manifest last keeps interrupted backups out of the UI.
        fs::write(dest.join("snapshot.txt"), manifest)?;
        Ok(dest)
    }
    fn verified(&self, name: &str) -> io::Result<(PathBuf, Vec<PathBuf>)> {
        if !safe_relative(name) || Path::new(name).components().count() != 1 {
            return Err(error("Invalid backup name"));
        }
        let root = self.backups.join(name);
        if linked(&root)? || linked(&root.join("snapshot.txt"))? {
            return Err(error("Linked backup is not supported"));
        }
        let text = fs::read_to_string(root.join("snapshot.txt"))?;
        let mut lines = text.lines();
        if lines.next() != Some("INFAMOUS-SNAPSHOT-1") {
            return Err(error("Unknown backup format"));
        }
        let mut paths = Vec::new();
        for line in lines {
            let (hash, rel) = line
                .split_once('\t')
                .ok_or_else(|| error("Invalid backup manifest"))?;
            if !safe_relative(rel) {
                return Err(error("Unsafe backup path"));
            }
            let path = PathBuf::from(rel);
            let parts = path.components().collect::<Vec<_>>();
            if !settings_file(Path::new(rel))
                && !(parts.len() >= 3
                    && parts[0].as_os_str() == "saves"
                    && save_name(&parts[1].as_os_str().to_string_lossy()))
            {
                return Err(error("Backup contains an unsupported file"));
            }
            let mut parent = root.clone();
            for part in path.components() {
                parent.push(part);
                if linked(&parent)? {
                    return Err(error("Linked backup content is not supported"));
                }
            }
            if sha256_file(&root.join(&path))? != hash || paths.contains(&path) {
                return Err(error("Backup hash mismatch or duplicate file"));
            }
            paths.push(path);
        }
        if paths.is_empty() {
            return Err(error("Empty snapshot"));
        }
        Ok((root, paths))
    }
    pub fn restore(&self, name: &str, scope: Scope) -> io::Result<PathBuf> {
        let (source, paths) = self.verified(name)?;
        let paths = paths
            .into_iter()
            .filter(|p| {
                if settings_file(p) {
                    scope.settings()
                } else {
                    scope.saves()
                }
            })
            .collect::<Vec<_>>();
        if paths.is_empty() {
            return Err(error(
                "This backup has no content for the selected restore option",
            ));
        }
        // Preserve everything currently managed before preparing any replacements.
        let recovery = match self.snapshot(Scope::Both, "before-restore") {
            Ok(p) => p,
            Err(e)
                if !self.settings.exists()
                    && !self.settings.with_file_name("controls.ini").exists()
                    && self.list_saves()?.is_empty() =>
            {
                let _ = e;
                self.backups.clone()
            }
            Err(e) => return Err(e),
        };
        fs::create_dir_all(&self.saves)?;
        let transaction = format!(".restore-{}", id());
        let save_stage = self.saves.join(&transaction);
        fs::create_dir(&save_stage)?;
        let setting_stage = self
            .settings
            .parent()
            .ok_or_else(|| error("No settings parent"))?
            .join(&transaction);
        fs::create_dir_all(&setting_stage)?;
        let mut targets = Vec::<(PathBuf, PathBuf, PathBuf)>::new();
        for rel in paths {
            if settings_file(&rel) {
                let stage = setting_stage.join(&rel);
                copy_file(&source.join(&rel), &stage)?;
                targets.push((
                    stage,
                    self.settings.with_file_name(&rel),
                    setting_stage.join(format!("{}.before", rel.display())),
                ));
            } else {
                let local = rel.strip_prefix("saves").unwrap();
                let name = local.components().next().unwrap().as_os_str();
                copy_file(&source.join(&rel), &save_stage.join("new").join(local))?;
                let target = self.saves.join(name);
                if !targets.iter().any(|(_, t, _)| *t == target) {
                    targets.push((
                        save_stage.join("new").join(name),
                        target,
                        save_stage.join("old").join(name),
                    ));
                }
            }
        }
        replace_paths(&targets, &recovery, |from, to| fs::rename(from, to))?;
        // Keep replaced originals in transaction folders as an additional recovery copy.
        Ok(recovery)
    }
}

fn replace_paths(
    targets: &[(PathBuf, PathBuf, PathBuf)],
    recovery: &Path,
    mut rename: impl FnMut(&Path, &Path) -> io::Result<()>,
) -> io::Result<()> {
    let mut applied: Vec<(PathBuf, PathBuf, PathBuf, bool)> = Vec::new();
    for (new, target, old) in targets {
        let existed = target.exists();
        let result = (|| {
            if existed {
                if linked(target)? {
                    return Err(error("Linked restore destination is not supported"));
                }
                fs::create_dir_all(old.parent().unwrap())?;
                rename(target, old)?;
            }
            rename(new, target)
        })();
        if let Err(e) = result {
            let mut rollback_error = existed && old.exists() && rename(old, target).is_err();
            for (new, target, old, existed) in applied.into_iter().rev() {
                if rename(&target, &new).is_err() {
                    rollback_error = true;
                }
                if existed && rename(&old, &target).is_err() {
                    rollback_error = true;
                }
            }
            return Err(error(format!(
                "Restore failed: {e}. Recovery: {}. Rollback incomplete: {rollback_error}",
                recovery.display()
            )));
        }
        applied.push((new.clone(), target.clone(), old.clone(), existed));
    }
    Ok(())
}

/// Reserve the same named object as the game's single-instance guard. Holding it
/// blocks a concurrent game launch until the snapshot operation has completed.
pub struct GameLock(#[cfg(windows)] *mut core::ffi::c_void);
#[cfg(windows)]
#[link(name = "kernel32")]
extern "system" {
    fn CreateMutexW(a: *const core::ffi::c_void, b: i32, n: *const u16) -> *mut core::ffi::c_void;
    fn GetLastError() -> u32;
    fn CloseHandle(h: *mut core::ffi::c_void) -> i32;
}
impl GameLock {
    pub fn acquire() -> io::Result<Self> {
        #[cfg(windows)]
        unsafe {
            let name = "Local\\inFamousRecomp.Game\0"
                .encode_utf16()
                .collect::<Vec<_>>();
            let handle = CreateMutexW(std::ptr::null(), 0, name.as_ptr());
            let last = GetLastError();
            if handle.is_null() {
                return Err(io::Error::from_raw_os_error(last as i32));
            }
            if last == 183 {
                CloseHandle(handle);
                return Err(error("Close the game before backing up or restoring data."));
            }
            Ok(Self(handle))
        }
        #[cfg(not(windows))]
        Ok(Self())
    }
}
impl Drop for GameLock {
    fn drop(&mut self) {
        #[cfg(windows)]
        unsafe {
            CloseHandle(self.0);
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    struct Fixture(PathBuf);
    impl Fixture {
        fn new() -> Self {
            let path = std::env::temp_dir().join(format!("infamous-snapshots-{}", id()));
            fs::create_dir(&path).unwrap();
            Self(path)
        }
        fn manager(&self) -> Manager {
            Manager {
                saves: self.0.join("savedata"),
                settings: self.0.join("settings.ini"),
                backups: self.0.join("backups"),
            }
        }
    }
    impl Drop for Fixture {
        fn drop(&mut self) {
            if let Ok(path) = self.0.canonicalize() {
                if path.starts_with(std::env::temp_dir().canonicalize().unwrap())
                    && path
                        .file_name()
                        .unwrap()
                        .to_string_lossy()
                        .starts_with("infamous-snapshots-")
                {
                    let _ = fs::remove_dir_all(path);
                }
            }
        }
    }
    fn write(path: PathBuf, value: &str) {
        fs::create_dir_all(path.parent().unwrap()).unwrap();
        fs::write(path, value).unwrap();
    }
    #[test]
    fn selective_restore_preserves_other_data_and_creates_recovery() {
        let f = Fixture::new();
        let m = f.manager();
        let save = m.saves.join("BCUS98119_INFAMOUS_0/DATA");
        write(save.clone(), "old progress");
        write(m.settings.clone(), "WINDOW_SIZE=720p");
        write(m.settings.with_file_name("controls.ini"), "fire=LMB");
        write(m.saves.join("OTHER_GAME/DATA"), "unrelated");
        let backup = m.backup(Scope::Both).unwrap();
        let name = backup.file_name().unwrap().to_str().unwrap();
        write(save.clone(), "new progress");
        write(m.settings.clone(), "WINDOW_SIZE=1080p");
        write(m.settings.with_file_name("controls.ini"), "fire=RMB");
        let recovery = m.restore(name, Scope::Settings).unwrap();
        assert_eq!(fs::read_to_string(&save).unwrap(), "new progress");
        assert_eq!(fs::read_to_string(&m.settings).unwrap(), "WINDOW_SIZE=720p");
        assert_eq!(
            fs::read_to_string(m.settings.with_file_name("controls.ini")).unwrap(),
            "fire=LMB"
        );
        assert!(recovery.join("snapshot.txt").exists());
        m.restore(name, Scope::Saves).unwrap();
        assert_eq!(fs::read_to_string(&save).unwrap(), "old progress");
        assert_eq!(
            fs::read_to_string(m.saves.join("OTHER_GAME/DATA")).unwrap(),
            "unrelated"
        );
    }
    #[test]
    fn corrupt_and_unsafe_snapshots_never_replace_existing_data() {
        let f = Fixture::new();
        let m = f.manager();
        let save = m.saves.join("BCUS98119_INFAMOUS_0/DATA");
        write(save.clone(), "progress");
        let backup = m.backup(Scope::Saves).unwrap();
        let name = backup.file_name().unwrap().to_str().unwrap();
        fs::write(backup.join("saves/BCUS98119_INFAMOUS_0/DATA"), "damaged").unwrap();
        assert!(m.restore(name, Scope::Saves).is_err());
        fs::write(
            backup.join("snapshot.txt"),
            "INFAMOUS-SNAPSHOT-1\n123\t../outside\n",
        )
        .unwrap();
        assert!(m.restore(name, Scope::Both).is_err());
        assert!(m.restore("../escape", Scope::Saves).is_err());
        assert_eq!(fs::read_to_string(save).unwrap(), "progress");
    }
    #[test]
    fn restoring_to_a_new_install_and_missing_scope() {
        let f = Fixture::new();
        let m = f.manager();
        write(m.settings.clone(), "settings");
        let backup = m.backup(Scope::Settings).unwrap();
        let name = backup.file_name().unwrap().to_str().unwrap();
        assert!(m.restore(name, Scope::Saves).is_err());
        let other = Manager {
            saves: f.0.join("new/saves"),
            settings: f.0.join("new/settings.ini"),
            backups: m.backups.clone(),
        };
        other.restore(name, Scope::Settings).unwrap();
        assert_eq!(fs::read_to_string(other.settings).unwrap(), "settings");
    }
    #[test]
    fn replacement_failure_rolls_back_all_applied_paths() {
        let f = Fixture::new();
        let mut paths = Vec::new();
        for i in 0..2 {
            let new = f.0.join(format!("new{i}"));
            let target = f.0.join(format!("target{i}"));
            let old = f.0.join(format!("old{i}"));
            write(new.clone(), "incoming");
            write(target.clone(), "original");
            paths.push((new, target, old));
        }
        let mut calls = 0;
        let result = replace_paths(&paths, &f.0, |from, to| {
            calls += 1;
            if calls == 4 {
                Err(error("injected rename failure"))
            } else {
                fs::rename(from, to)
            }
        });
        assert!(result.is_err());
        for (_, target, _) in paths {
            assert_eq!(fs::read_to_string(target).unwrap(), "original");
        }
    }
    #[test]
    fn no_saves_is_valid_and_incomplete_backups_are_hidden() {
        let f = Fixture::new();
        let m = f.manager();
        assert!(m.list_saves().unwrap().is_empty());
        assert!(m.backup(Scope::Both).is_err());
        assert!(m.list_backups().unwrap().is_empty());
        assert_eq!(stamp(UNIX_EPOCH), "1970-01-01 00:00 UTC");
    }
}
