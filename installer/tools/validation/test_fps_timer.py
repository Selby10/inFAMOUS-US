"""Run the limiter's timer wait against scripted Win32 results."""
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest


HARNESS = r'''
#include <cassert>
#include <chrono>
#include <cstdio>
#include <cstring>

using HANDLE = void*;
using DWORD = unsigned long;
using LONGLONG = long long;
struct LARGE_INTEGER { LONGLONG QuadPart; };
constexpr int FALSE = 0;
constexpr DWORD WAIT_OBJECT_0 = 0, WAIT_TIMEOUT = 258, WAIT_FAILED = 0xffffffff;
constexpr DWORD ERROR_TIMEOUT = 1460;

namespace limiter_test {
struct Clock {
    using time_point = ::std::chrono::steady_clock::time_point;
    static time_point now() { return {}; }
};

static int timer, arms, closes, sleeps;
static HANDLE cap_waitable_timer = &timer;
static bool arm_succeeds = true;
static DWORD wait_result = WAIT_OBJECT_0;
static Clock::time_point slept_until;

namespace std {
using namespace ::std;
namespace this_thread {
void sleep_until(Clock::time_point wake) { ++sleeps; slept_until = wake; }
}
}

bool SetWaitableTimer(HANDLE handle, LARGE_INTEGER*, int, void*, void*, int) {
    assert(handle == &timer);
    ++arms;
    return arm_succeeds;
}
DWORD WaitForSingleObject(HANDLE, DWORD) { return wait_result; }
DWORD GetLastError() { return 6; }
void CloseHandle(HANDLE handle) { assert(handle == &timer); ++closes; }
'''

CHECK = r'''
}  // namespace limiter_test

int main(int argc, char** argv) {
    using namespace limiter_test;
    assert(argc == 2);
    const auto wake = Clock::now() + ::std::chrono::milliseconds(10);
    if (::std::strcmp(argv[1], "timeout") == 0) {
        wait_result = WAIT_TIMEOUT;
        cap_hires_wait_until(wake);
        assert(closes == 0 && cap_waitable_timer == &timer);
        assert(sleeps == 1 && slept_until == wake);

        wait_result = WAIT_OBJECT_0;
        cap_hires_wait_until(wake);
        assert(arms == 2 && sleeps == 1);
    } else {
        arm_succeeds = ::std::strcmp(argv[1], "arm_failure") != 0;
        wait_result = WAIT_FAILED;
        cap_hires_wait_until(wake);
        assert(closes == 1 && cap_waitable_timer == nullptr);
        assert(sleeps == 1 && slept_until == wake);
    }
}
'''


class FpsTimerTests(unittest.TestCase):
    def test_transient_timeout_reuses_timer_and_api_failure_disables_it(self):
        compiler = shutil.which('cl') or shutil.which('clang++') or shutil.which('g++')
        if not compiler:
            self.skipTest('Run in a VS Developer Command Prompt, or put clang++/g++ on PATH')
        root = Path(__file__).resolve().parents[3]
        source = (root / 'infamous-recomp/infamous_clock_probe.h').read_text(encoding='utf-8')
        begin = source.index('static void cap_hires_wait_until(')
        end = source.index('\n}\n', begin) + 3
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            unit = directory / 'timer.cpp'
            exe = directory / 'timer.exe'
            unit.write_text(HARNESS + source[begin:end] + CHECK, encoding='utf-8')
            if Path(compiler).stem.lower() == 'cl':
                args = [compiler, '/nologo', '/std:c++17', str(unit), '/Fe:' + str(exe)]
            else:
                args = [compiler, '-std=c++17', str(unit), '-o', str(exe)]
            built = subprocess.run(args, cwd=directory, capture_output=True, text=True)
            self.assertEqual(built.returncode, 0, built.stdout + built.stderr)
            for scenario in ('timeout', 'arm_failure', 'wait_failure'):
                with self.subTest(scenario=scenario):
                    result = subprocess.run([str(exe), scenario], cwd=directory, capture_output=True, text=True)
                    self.assertEqual(result.returncode, 0, result.stderr)
                    if scenario != 'timeout':
                        self.assertIn('Win32 6', result.stderr)


if __name__ == '__main__':
    unittest.main()
