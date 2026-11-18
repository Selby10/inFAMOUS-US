# infamous-installer (US Project - BCUS98119)

Builds the inFamous (US `BCUS98119` / European `BCES00220`) PC port from the player's **own** copy of the game. The port's
repository holds no game code and no game data. The installer regenerates all of it from the
player's dump, applies the region-specific patches (from `installer/regions/BCUS98119/`), compiles it and writes a launcher.

It's written in Rust. The default `gui` feature enables `eframe` and `rfd` for the setup
window; Windows builds also depend on `windows-sys` for the launcher dialog. Building with
`--no-default-features` omits the setup GUI and its dependencies, but Windows builds still
require `windows-sys`. See `Cargo.toml` for the dependency versions and enabled features.

Offline builds require the Rust toolchain and all required Cargo dependencies to be available
locally. Otherwise, Cargo needs network access to download missing dependencies. Build with:

```bat
cd installer
cargo build --release
:: -> installer\target\release\infamous-installer.exe
```

## For players

You need:

* **Your inFamous disc dump**: the folder that contains `PS3_GAME` (US release `BCUS98119`).
* **Python 3.10+** from python.org, with "Add python.exe to PATH" ticked.
* **Visual Studio 2022 Build Tools** with the *Desktop development with C++* workload and the
  *C++ Clang Compiler for Windows* component.
* **RPCS3**, if your `PS3_GAME\USRDIR` has only `EBOOT.BIN` and no decrypted `EBOOT.elf`.
* Roughly 10 GB of free disk space and 16 GB of RAM. The generated C++ files are large.

Double-click `infamous-installer.exe`, or run it from a terminal:

```bat
infamous-installer --game "D:\Dumps\inFamous" --rpcs3 "C:\RPCS3\rpcs3.exe"
infamous-installer doctor          :: check the prerequisites only
```

The installer runs these steps:

1. It reads `PARAM.SFO` and refuses any game that isn't BCES00220 or BCUS98119.
2. It decrypts `EBOOT.BIN` to `EBOOT.elf` with `rpcs3 --decrypt` if needed. You can also do this
   yourself in RPCS3 with *Utilities → Decrypt PS3 Binaries*.
3. It checks the SHA-256 of `EBOOT.elf` against the version the port was made from.
4. It links your dump into the checkout (a directory junction, so nothing is copied).
5. It creates a private Python environment in `.installer\venv` and installs the toolkit's packages.
6. It runs the generation pipeline: PPU lift, SPU images, job manager, the 21 WWS job modules, the
   plugin blobs, the SPU post-passes and the UI assets.
7. It checks every generated file against the hash recorded by the maintainer, then applies
   `installer/patches/` and checks again. The result is byte-identical to the maintainer's tree.
8. It runs CMake and Ninja with clang-cl, using the same settings as `build.cmd`.
9. It writes `Play inFamous.cmd`.

Finished steps are skipped when you run the installer again, so an interrupted install continues
where it stopped. `--regen` starts the pipeline over.

### Updating an installation

```bat
infamous-installer update            :: download what changed on GitHub, rebuild what that needs
infamous-installer update --check    :: only report whether an update is available
```

The repository is set in `infamous.toml` (`[update] repo / branch / path`). The updater asks
GitHub for the branch's latest commit and its file list (two API requests), compares each file's
git blob hash with the local file (a CRLF copy of an LF file counts as equal), downloads only the
differing files from raw.githubusercontent.com (or the commit's zip when more than 150 changed),
verifies every download against its blob hash, then moves them into place. Files an earlier update
installed that the repository no longer has are removed; `.installer/update.state` records the
commit and file list. If `generated.lock` no longer matches what is on disk, the game code is
regenerated from the player's files and re-patched; generated files that come out identical keep
their timestamps, so only changed translation units recompile. HTTP goes through `curl.exe` and
zips through `tar.exe` (both part of Windows 10/11); a private repository needs `GITHUB_TOKEN`.
If the repository carries `installer/bin/infamous-installer.exe` or `installer/bin/infamous-launch.exe`,
those replace the installer and the game's launcher too (a running copy is renamed to `.old`).

## The setup program (what players download)

Download the `inFamous-Setup` artifact from a successful **Setup app** workflow run and open
`inFamous Setup.exe`. Select your own US (`BCUS98119`) or European (`BCES00220`) disc image
or extracted game folder, then click **Install**. Setup fills in an install location and lets you
change it or turn off shortcuts on the same screen.

Setup checks the game and free space, installs missing Python and C++ build tools, and builds the
game on your PC. Windows may ask for approval. If your ISO is encrypted, keep its matching
`.dkey` next to the ISO or select the key in Setup. If the game executable requires RPCS3 and
Setup cannot find it, select your local `rpcs3.exe`. When the build finishes, click **Play**.
Run Setup again with the same game and location to reuse a complete copy of the disc and the
incremental build. Setup shows details if a step fails so you can correct the issue and retry.

Build it (maintainer, after `make-patches`):

```bat
cd installer && cargo build --release
cd .. && installer\target\release\infamous-installer package --out "dist\inFamous Setup.exe"
```

Disc images: `infamous-installer extract-iso --iso game.iso --out DIR [--dkey game.dkey]` is the
same extractor on the command line. RPCS3 (`rpcs3 --decrypt`) unlocks EBOOT.BIN; the setup finds
rpcs3.exe in the usual places or asks for it.

## US Gigawatt Blades DLC

After building the base game, import your US DLC package and matching RAP with
the installer's Python environment (or another Python with `pycryptodome`):

```bat
.installer\venv\Scripts\python installer\tools\import_gigawatt.py --pkg "D:\DLC\Gigawatt.pkg" --rap "D:\DLC\UP9000-BCUS98119_00-SMLAUNCHPROMO000.rap" --hdd-root "D:\Games\inFAMOUS-US\HDD"
```

The helper verifies the content ID and EDAT block authentication before installing
the sentinel and its private license sidecar. It preserves existing title metadata
and saves. A mismatched RAP or damaged sentinel fails before installation.
Keep the original PKG/RAP and the installed HDD tree outside source control.

All launch paths must inherit the same `PS3_HDD0_ROOT`. Use a private wrapper next
to `Play inFamous.cmd`, including after updates:

```bat
@echo off
setlocal
set "PS3_HDD0_ROOT=D:\Games\inFAMOUS-US\HDD"
call "%~dp0Play inFamous.cmd"
```

For diagnostics, call `play-dred.cmd` from the same wrapper instead. If launching
the graphical `inFamous.exe`, start it from this environment too. Import success
verifies the file/license pair; verify the power in gameplay and after a restart.

## For the maintainer

The patch set has to be created once on the development tree, and again whenever the
maintained generated files change:

```bat
infamous-installer make-patches --game "D:\Dumps\inFamous"
infamous-installer export --out D:\publish\infamous-port
```

* `make-patches` exports a clean tree to `.installer\fresh`, which is exactly what a player
  downloads. It runs the pipeline there, compares the output with your `infamous-recomp`, and
  writes `installer/generated.lock` and `installer/patches/`. It doesn't change any of your files.
* It refuses to finish if publishing a patch would amount to publishing generated content:
  * A binary file differs from the pipeline's output.
  * The pipeline doesn't produce a file at all.
  * A patch carries more than half of a file.

  Each of these means a pipeline step is missing or uses different arguments than you used.
  Fix that step in `infamous.toml` and run `make-patches` again. `--keep-fresh` skips the
  re-export and `--regen` re-runs every step.
* `export` copies the publishable tree: ps3recomp, infamous-recomp and the installer, without
  `[generated]` files and without anything in `[export].exclude` (backups, dumps, logs, the game
  folder).

Everything about the game and the port is in `infamous.toml`: pipeline steps, which files count
as generated, build commands, launcher switches and export rules. You can change it without
rebuilding the installer.

### Other releases

A release with its own executable has a folder in `installer/regions/<title id>/`, holding its own
`generated.lock` and any patches that differ from `installer/patches/`.
`make-patches` only writes the European set; see [the US notes](regions/BCUS98119/README.md).

### Line endings

Python's text mode writes CRLF on Windows and LF elsewhere, and the tree mixes both: the lifted PPU
units are CRLF except `ppu_recomp_000.cpp`, and the SPU sources are LF while `spu_v200.py`,
`spu_lifter.py` and `plug_lift.py` write CRLF on Windows. When a pipeline file and the maintained
file use different endings throughout, the patch starts with `# eol lf` (or `# eol crlf`): the
installer converts the file's endings first and then applies the hunks, so the patch holds only the
real edits instead of every line. Run `make-patches` on Windows, because that is what players run.


### Launcher save management

The launcher resolves `SAVEDATA_ROOT` with the same launch environment as the game;
without an override it uses `<game working folder>/gamedata/dev_hdd0/home/00000001/savedata`.
It does not infer saves from `PS3_HDD0_ROOT`, because the runtime's save subsystem
uses its own override. Settings stay in the working folder. Backup/restore holds
the game's named instance guard so a concurrent game cannot start mid-operation.
Snapshots include only BCUS98119/BCES00220 save directories and optional settings/
keybindings. No DLC/license files or generated assets are bundled. Restore verifies
hashes and rejects unsafe paths or linked content before replacing data, stages
replacements on the destination volume, and rolls back committed replacements on
failure. Recovery snapshots and original transaction files are retained.

### Credits and in-game save slots

The Settings/Saves manager identifies this US ISO fork as
[WoofahRayetCode/inFAMOUS-US](https://github.com/WoofahRayetCode/inFAMOUS-US),
credits [Whompay/inFAMOUSreborn](https://github.com/whompay/inFAMOUSreborn) for the
original European ISO port (`BCES00220`), and links the ps3recomp foundation.

With the shipped `SAVEDATA_NATIVE_UI=1`, the game's explicit Save Game/Load Game
operations open a slot selector. US and EU saves retain their region-specific
`*_INFAMOUS_0` autosave and `*_INFAMOUS_1` existing manual save; manual slots 2-10
use the same prefix and remain discoverable by the manager and its backups.
Save lists all ten manual slots; Load lists populated slots plus autosave.
Overwrite requires confirmation. AutoSave/AutoLoad stay noninteractive and retain
their original paths. Setting `SAVEDATA_NATIVE_UI=0` preserves headless behavior.
