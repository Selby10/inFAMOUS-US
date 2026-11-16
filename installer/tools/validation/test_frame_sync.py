"""Exercise the real frame-sync function against a deterministic GPU queue.

Run from a VS x64 Developer Command Prompt (cl), or with clang/gcc on PATH.
No game, driver, or SDK is needed. Only the function under test is extracted;
the mock models queued signals separately from completed fence values.
"""
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest


MOCK = r'''
#include <stdint.h>
#include <stdio.h>
typedef uint32_t u32;
typedef uint64_t u64;
typedef long HRESULT;
typedef struct Fake Fake;
typedef struct {
    HRESULT (*Signal)(Fake*, Fake*, u64);
    u32 (*GetCurrentBackBufferIndex)(Fake*);
    u64 (*GetCompletedValue)(Fake*);
    HRESULT (*SetEventOnCompletion)(Fake*, u64, void*);
    HRESULT (*GetDeviceRemovedReason)(Fake*);
} Vtable;
struct Fake { Vtable* lpVtbl; };
static u64 completed, queued, target;
static int failure;
static u32 next_index, waits;
static HRESULT signal_queue(Fake* q, Fake* f, u64 value) { queued = value; return failure == 1 ? -1 : 0; }
static u32 buffer_index(Fake* s) { return next_index; }
static u64 completed_value(Fake* f) { return failure == 3 ? UINT64_MAX : completed; }
static HRESULT set_event(Fake* f, u64 value, void* e) { target = value; return failure == 2 ? -1 : 0; }
static HRESULT removed_reason(Fake* d) { return failure == 5 ? -1 : 0; }
#include <stdlib.h>
#define FAILED(x) ((x)<0)
#define E_FAIL (-1)
#define WAIT_OBJECT_0 0
#define WAIT_TIMEOUT 258
typedef unsigned long DWORD;
static int timeouts;
static void d3d12_sync_fatal(const char* where, HRESULT hr) { exit(91); }
static int WaitForSingleObject(void* e, int timeout) {
    waits++;
    if (failure == 4) return 0xFFFFFFFF;
    if (failure == 5) return WAIT_TIMEOUT;
    if (timeouts > 0) { timeouts--; return WAIT_TIMEOUT; }
    completed = target;
    return 0;
}
static Vtable vtable = {signal_queue, buffer_index, completed_value, set_event, removed_reason};
static Fake object = { &vtable };
static struct {
    u32 frame_index;
    u64 fence_values[2];
    Fake *cmd_queue, *fence, *swap_chain, *device;
    void* fence_event;
} s_d3d = {0, {0, 0}, &object, &object, &object, &object, 0};
'''

CHECK = r'''
int main(int argc, char** argv) {
    if (argc > 1) { failure = atoi(argv[1]); move_to_next_frame(); return 9; }
    /* The prepass GPU wait completed value 7, then a new list was submitted.
     * Offscreen rendering/occlusion leaves the backbuffer index unchanged. */
    completed = 7;
    s_d3d.fence_values[0] = 7;
    next_index = 0;
    move_to_next_frame();
    if (queued <= 7 || waits != 1 || completed < queued) return 1;
    /* An actual backbuffer change waits for that slot's outstanding work,
     * without requiring the newly submitted frame to finish first. */
    completed = 10;
    s_d3d.fence_values[0] = 12;
    s_d3d.fence_values[1] = 11;
    next_index = 1;
    waits = 0;
    move_to_next_frame();
    if (queued <= 12 || target != 11 || waits != 1) return 2;
    if (s_d3d.frame_index != 1 || s_d3d.fence_values[1] <= queued) return 3;
    completed = 0; waits = 0; timeouts = 1;
    wait_for_fence(20);
    if (waits != 2 || completed < 20) return 4;
    return 0;
}
'''


class FrameSyncTests(unittest.TestCase):
    def test_submission_has_a_fresh_fence_even_without_present(self):
        compiler = shutil.which('cl') or shutil.which('clang') or shutil.which('gcc')
        if not compiler:
            self.skipTest('Run in a VS Developer Command Prompt, or put clang/gcc on PATH')
        root = Path(__file__).resolve().parents[3]
        source = (root / 'ps3recomp/libs/video/rsx_d3d12_backend.c').read_text(encoding='utf-8')
        begin = source.index('static void move_to_next_frame(void)\n{')
        end = source.index('\n}\n', begin) + 3
        helper = source.index('static void wait_for_fence(u64 target)\n{')
        helper_end = source.index('\n}\n', helper) + 3
        function = source[helper:helper_end] + source[begin:end]
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            unit = directory / 'sync.c'
            exe = directory / 'sync.exe'
            unit.write_text(MOCK + function + CHECK)
            if Path(compiler).stem.lower() == 'cl':
                args = [compiler, '/nologo', '/TC', str(unit), '/Fe:' + str(exe)]
            else:
                args = [compiler, str(unit), '-o', str(exe)]
            built = subprocess.run(args, cwd=directory, capture_output=True, text=True)
            self.assertEqual(built.returncode, 0, built.stdout + built.stderr)
            self.assertEqual(subprocess.run([str(exe)], cwd=directory).returncode, 0)
            for failure in range(1, 6):
                with self.subTest(failure=failure):
                    self.assertEqual(subprocess.run([str(exe), str(failure)], cwd=directory).returncode, 91)


if __name__ == '__main__':
    unittest.main()
