# Native Windows validation

Use Python 3.11+ and a PowerShell host permitted to run local scripts (PowerShell
7 is preferred; `--powershell` selects its executable). No execution policy is
changed by these tools. Keep evidence bundles outside the
repository: they contain machine paths and game logs. Do not publish saves,
memory dumps, license material, or generated game files.

`run_validation.py` launches the existing `build-install` executable with the
manifest's environment, the installed `settings.ini`, and explicit private HDD
and save roots. It refuses to run alongside another game process. It records
the executable SHA-256, commit/diff summary, launch environment, settings,
driver, scenario, GPU samples, exit status and both output streams. GPU samples
do not establish a CPU bottleneck; CPU temperature is explicitly uncollected.

Example (adapt the paths):

```powershell
python installer/tools/validation/run_validation.py --root C:/Games/inFAMOUS-US --elf D:/Games/inFAMOUS-US/Disc/PS3_GAME/USRDIR/EBOOT.elf --hdd-root D:/Games/inFAMOUS-US/HDD --output-root C:/Tools/inFAMOUS/captures --scenario opening-warm --seconds 1800 --presentmon C:/Tools/inFAMOUS/PresentMon.exe
```

PresentMon collection requests normal Windows UAC approval. Only its collector
is elevated. Cancelling approval is recorded as failed collection; it never
counts as a passing capture. Omitting `--presentmon` collects logs and GPU
samples only. Close the game normally to finalize the bundle. The duration
limits profiling, not gameplay; the game remains available until you close it.

Manual captures clear inherited `PAD_FILE`, `PAD_SCRIPT`, `PAD_AUTOPRESS` and
`PAD_STICK` automation. Use `--scripted-input` only for automated bench input;
it creates an empty `pad.txt` command channel. Writing four stick values holds
those values indefinitely, including `128 128 128 128`: that centres the sticks
and overrides the physical controller. Restart after a scripted stick test
before using a physical controller for acceptance.

Use `--mode timing` for existing CPU/render/SPURS telemetry and GPU timestamps;
use `--mode dred` for device-removal diagnostics, or `--mode debug` to add the
installed D3D12 debug layer and error queue (availability is logged). These runs are diagnostic and
must not replace uninstrumented performance acceptance. `--env NAME=VALUE`
overrides one existing manifest variable for controlled comparisons; it also accepts
the existing opt-in renderer experiments `TEXCS`, `TEXCS_THREADS`, `TEXCS_EARLY`,
`TEXCS_PHASE`, `TEXCS_PHASE_DIV`, `TEXCS_ECORE` and `TEX_FAST_DECODE`. Their values
are recorded in `run.json`, including inherited values. It never
rewrites the launcher. Do not vary multiple settings in one comparison.

Rebuild the renderer before using these scripts: older builds enable DRED even
for `D3D12_DRED=0`. The corrected build explicitly logs `DRED disabled` in normal
mode. `run.json` records the observed DRED/debug-layer state and verifies normal
mode only when that disabled message is present. This flag confirms the mode,
not frame-time or gameplay acceptance. Device-loss logs override a zero game
exit code; a missing/empty CSV remains a failed capture.

Use `--cache cold` only with the game closed. The runner renames the original
shadercache inside the port directory, keeps the newly produced cold cache,
and restores the original when the game exits (also on handled interruption).
If the supervisor is killed or the PC loses power, close the game and recover
`shadercache-before-<run>` manually before the next run; neither cache is deleted.

Record boundaries from a second terminal:

```powershell
python installer/tools/validation/mark_run.py C:/Tools/inFAMOUS/captures/<run> gameplay-start
python installer/tools/validation/mark_run.py C:/Tools/inFAMOUS/captures/<run> street-start
```

Markers give UTC and approximate collector-relative time. Collector startup
adds a small offset: inspect the route and CSV before selecting exact analysis
boundaries. The initial summary includes menus/loading and is not an acceptance
result. After collection finishes and releases the CSV:

```powershell
python installer/tools/validation/analyze_frames.py C:/Tools/inFAMOUS/captures/<run>/frames.csv --start 30 --end 180
python -m unittest discover -s installer/tools/validation -p "test_*.py"
```

Run the tests from a VS x64 Developer Command Prompt to include the native
fence regression (otherwise it skips when no C compiler is on PATH). That test
compiles the actual frame-sync function with a simulated GPU queue and checks
both an unchanged backbuffer index and a backbuffer change. It requires no dump
or graphics device. It does not establish that a particular device hang is fixed.

`test_texture_jobs.py` pauses a real checksum worker and verifies that its job
buffer cannot be republished while it is reading. `test_telemetry.py` blocks the
host CPU reporter and verifies that guest requests return independently, with
bounded pending work. Windows thread enumeration runs on a helper thread;
`[TELEM-CPU]` records its duration. These diagnostics remain off in normal mode.

For a short hitch investigation, use `--mode timing --env PS3_PROF_MAIN=10
--env PS3_PROF_MAIN_ONLY=1 --env HITCH_MS=60 --env HITCH_MAX=8
--env SPIKE_MS=50 --env SPIKE_MAX=40`. These are arguments to the run command
above; the main sampler and timing instrumentation add overhead. All four hitch
threshold/limit controls are accepted and recorded. Repeat with normal mode
before claiming a gameplay improvement. Guest sampling identifies where a
thread was observed; it does not by itself prove the cause of a stalled frame.

`test_controller_input.py` also needs clang and the VS x64 SDK environment. It
compiles the actual assignment, overlay and actuator functions against simulated
devices, exercises the SDL bridge with mocked devices, and runs a virtual gamepad
through the bundled SDL DLL. It checks mixed-device hotplug, stale rumble requests,
binary small-motor normalization, sensor conversion/capabilities and cleanup.
Virtual controllers exist only inside the test process. These tests do not verify
physical USB/Bluetooth transports, motor feel, sensor calibration or gameplay.

For the playable milestone use 1920x1080, cap 60, upscaling off, FG off and the
same save/checkpoint for each comparison. First complete automated diagnostics;
then manually play equivalent 30-minute cold/warm routes containing street
traversal, combat and rooftops. Require present-interval p99 <=20 ms in marked
gameplay, report average/worst/hitch counts and display intervals too, and require
no device loss, crash, softlock or save/progression regression. Verify audio,
keyboard/mouse and controller, vegetation/streaming, earn 600 XP naturally,
buy/use Gigawatt Blades, save/restart and verify the purchased power. A short
opening run or a nonreproduced hang does not satisfy this milestone.


### Automated idle checks

Use `--auto-close` to request normal window closure at the collection deadline.
If closure fails, the runner terminates its own game after 15 seconds and records
`forced_cleanup`; this is not a clean-exit acceptance result. `--collector-direct`
uses existing ETW permissions without a UAC prompt; insufficient permissions remain
a failed collection. `--scripted-input --hold-neutral` centers the sticks for an
idle checkpoint comparison. It does not simulate a street/combat/rooftop route.
`--env BENCH_SHOT=<private request path>` enables the existing runtime screenshot
channel; create that file to capture `<path>.bmp`. Never publish these captures.

For baseline comparisons run three 180-second captures per warm/cold cache condition
with 1080p, cap 60, upscaling off and FG off. Analyze seconds 30-180 only after
confirming the same loaded checkpoint and successful collection. Device failure,
forced cleanup, missing/empty frame data, or unverified normal mode prevent a pass.
Optional DLAA/DLSS/FSR/FG modes require their SDKs and supported hardware; a missing
SDK is untested, not a successful fallback test. Keep changes to one setting per run.


`run_matrix.py` accepts `--root`, `--elf`, `--hdd-root`, `--output-root` and
`--presentmon`. It runs three cold and three warm idle-checkpoint captures,
requests closure after each, and writes `matrix-summary.json`. Supply a private
HDD/save copy. It never elevates the collector or changes execution policy.
Collection errors and incomplete intervals cannot pass; device loss or forced
cleanup stops the series. Stop the supervisor and close its validation-owned game,
then verify cache restoration before handing the game back for manual play.
Do not start a normal game alongside a benchmark. A passing idle matrix still does
not replace marked traversal/combat/rooftop acceptance.
