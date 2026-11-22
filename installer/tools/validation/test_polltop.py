"""Exercise the timer syscall with scripted Win32 wait durations and QPC."""
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest


HARNESS = r'''
#include "ps3recomp/runtime/syscalls/sys_timer.h"
#include <assert.h>
#include <stdio.h>
#include <stdlib.h>

static PPU_THREAD_LOCAL unsigned long long now_us, wait_us;
static PPU_THREAD_LOCAL unsigned qpc_reads;

static BOOL test_frequency(LARGE_INTEGER* value) {
    value->QuadPart = 2500000;
    return TRUE;
}
static BOOL test_counter(LARGE_INTEGER* value) {
    ++qpc_reads;
    value->QuadPart = (LONGLONG)(now_us * 5 / 2);
    return TRUE;
}
static HANDLE test_timer(LPSECURITY_ATTRIBUTES a, LPCWSTR n, DWORD f, DWORD r) {
    (void)a; (void)n; (void)f; (void)r;
    return (HANDLE)1;
}
static BOOL test_arm(HANDLE timer, const LARGE_INTEGER* due, LONG period,
                     PTIMERAPCROUTINE callback, LPVOID arg, BOOL resume) {
    (void)timer; (void)period; (void)callback; (void)arg; (void)resume;
    assert(due->QuadPart == -15000);
    return TRUE;
}
static DWORD test_wait(HANDLE timer, DWORD timeout) {
    assert(timer == (HANDLE)1 && timeout == INFINITE);
    now_us += wait_us;
    return WAIT_OBJECT_0;
}
static void test_yield_processor(void) { now_us += wait_us; }

#define QueryPerformanceFrequency test_frequency
#define QueryPerformanceCounter test_counter
#define CreateWaitableTimerExW test_timer
#define SetWaitableTimer test_arm
#define WaitForSingleObject test_wait
#undef YieldProcessor
#define YieldProcessor test_yield_processor
#include "ps3recomp/runtime/syscalls/sys_timer.c"
#undef WaitForSingleObject

uint8_t* vm_base;
sys_event_queue_info g_sys_event_queues[SYS_EVENT_QUEUE_MAX];
volatile int g_ab_mask;
void ppu_log_host_chain(const char* label) { (void)label; }
int ps3_spurs_kick_us(void) { return -1; }
void ps3_spurs_kick(void) {}

static void sample(uint64_t tid, uint32_t lr, uint64_t requested, unsigned elapsed) {
    ppu_context context = {0};
    context.thread_id = tid;
    context.lr = lr;
    context.gpr[3] = requested;
    wait_us = elapsed;
    assert(sys_timer_usleep(&context) == CELL_OK);
}

static DWORD WINAPI worker(void* arg) {
    (void)arg;
    now_us = 6000000;
    sample(7, 0x1234, 6000, 2000);
    now_us = 7000000;
    sample(7, 0x1234, 8000, 3000);
    if (!getenv("PS3_POLLTOP"))
        assert(qpc_reads == 2);
    return 0;
}

int main(void) {
    now_us = 4000000;
    sample(7, 0x1234, 5000, 1800);
    sample(0x100000007ull, 0x1234, 6000, 2200);
    sample(7, 0x5678, 5, 6);
    now_us = 5000000;
    sample(7, 0x1234, 7000, 2400);
    if (!getenv("PS3_POLLTOP"))
        assert(qpc_reads == 6);
    HANDLE thread = CreateThread(NULL, 0, worker, NULL, 0, NULL);
    assert(thread && WaitForSingleObject(thread, INFINITE) == WAIT_OBJECT_0);
    DWORD status = 1;
    assert(GetExitCodeThread(thread, &status) && status == 0);
    CloseHandle(thread);
    return 0;
}
'''


class PolltopTests(unittest.TestCase):
    def test_guest_attribution_elapsed_time_and_disabled_clock_reads(self):
        compiler = shutil.which('clang-cl') or shutil.which('cl')
        if os.name != 'nt' or not compiler:
            self.skipTest('Run in a Visual Studio Developer Command Prompt on Windows')
        root = Path(__file__).resolve().parents[3]
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            source = directory / 'polltop.c'
            exe = directory / 'polltop.exe'
            source.write_text(HARNESS, encoding='utf-8')
            args = [compiler, '/nologo', '/std:c17', f'/I{root}', str(source), f'/Fe:{exe}']
            built = subprocess.run(args, cwd=directory, capture_output=True, text=True)
            self.assertEqual(built.returncode, 0, built.stdout + built.stderr)

            env = dict(os.environ)
            for name in ('PS3_POLLTOP', 'PS3_WAIT_OBJ', 'POLLSITE', 'PS3_SHORT_SLEEP_SPIN'):
                env.pop(name, None)
            env['PS3_USLEEP_CAP'] = '1234:1500'
            disabled = subprocess.run([exe], env=env, capture_output=True, text=True)
            self.assertEqual(disabled.returncode, 0, disabled.stderr)
            self.assertNotIn('[POLLTOP]', disabled.stderr)

            env['PS3_POLLTOP'] = '1'
            enabled = subprocess.run([exe], env=env, capture_output=True, text=True)
            self.assertEqual(enabled.returncode, 0, enabled.stderr)
            rows = [line.split(' ', 2) for line in enabled.stderr.splitlines()
                    if line.startswith('[POLLTOP] host_tid=')]
            self.assertEqual([row[2] for row in rows], [
                'start_us=4000000 end_us=5002400 duration_us=1002400 '
                'tid=7 lr=0x00001234 calls=2 requested_us=12000 elapsed_us=4200',
                'start_us=4000000 end_us=5002400 duration_us=1002400 '
                'tid=4294967303 lr=0x00001234 calls=1 requested_us=6000 elapsed_us=2200',
                'start_us=4000000 end_us=5002400 duration_us=1002400 '
                'tid=7 lr=0x00005678 calls=1 requested_us=5 elapsed_us=6',
                'start_us=6000000 end_us=7003000 duration_us=1003000 '
                'tid=7 lr=0x00001234 calls=2 requested_us=14000 elapsed_us=5000',
            ])
            self.assertEqual(rows[0][1], rows[1][1])
            self.assertEqual(rows[0][1], rows[2][1])
            self.assertNotEqual(rows[0][1], rows[3][1])


if __name__ == '__main__':
    unittest.main()
