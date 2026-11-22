"""Run the clock-paced mixer against a scripted guest, device, and clock."""
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import unittest


MOCK = r'''
#include <assert.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#ifdef _WIN32
#include <windows.h>
#endif

#define CELL_AUDIO_BLOCK_SAMPLES 256
#define CELL_AUDIO_SAMPLE_RATE 48000
#define ST_HS 768
#define ST_OV 256
#define NS_PER_MS 1000000ull
#define START_NS (10000 * NS_PER_MS)
#define LATE_NS (START_NS + 5000 * NS_PER_MS)

typedef uint32_t u32;
volatile int g_abx;
static int s_mix_thread_running = 1;
static unsigned s_fifo_r, s_fifo_w;
static unsigned long long s_n_pads, s_n_drops;
static unsigned long long s_n_late_waits, s_late_wait_us, s_n_late_gaps;
static unsigned long long s_n_plc_conceal, s_n_plc_fadein, s_n_stall, s_n_stall_giveup;
static unsigned long long s_st_steps, s_st_sx_steps, s_st_added;
static double s_st_rsum, s_st_rmax;
static int s_sent = 1;
static float s_mix_buffer[CELL_AUDIO_BLOCK_SAMPLES * 2] = {1.0f};

static uint64_t clock_ns = START_NS, mixed_ns, ready_ns, low_slack_ns, stop_ns;
static unsigned mixes, clock_reads, waits;
static uint64_t audio_now_ns(void) { ++clock_reads; return clock_ns; }
static void audio_sleep_short(uint64_t ns_left) { clock_ns += ns_left; }
static void mock_wait(void) {
    ++waits;
    clock_ns += NS_PER_MS;
    if (clock_ns >= stop_ns) s_mix_thread_running = 0;
}
#ifdef _WIN32
#define Sleep(ms) do { assert((ms) == 1); mock_wait(); } while (0)
#else
#define usleep(us) do { assert((us) == 250); mock_wait(); } while (0)
#endif
static int audio_ports_ready(void) { return clock_ns >= ready_ns; }
static int audio_ports_late(void) { return !audio_ports_ready(); }
static int audio_backend_queued(void) {
    return clock_ns < low_slack_ns ? 2 * CELL_AUDIO_BLOCK_SAMPLES : CELL_AUDIO_BLOCK_SAMPLES;
}
static void audio_feed_device(int primed) { assert(primed); s_fifo_r = s_fifo_w; }
static void audio_fifo_push(const float* block) { assert(block == s_mix_buffer); ++s_fifo_w; }
static void audio_mix_one_block(void) {
    ++mixes;
    if (mixes == 2) mixed_ns = clock_ns;
    if (mixes == 3) s_mix_thread_running = 0;
}
static void audio_notify_event_queues(void) {
    if (mixes == 1) clock_ns = LATE_NS;
    if (mixes == 2) clock_ns += 5000 * NS_PER_MS;
}
static int audio_stretch_on(void) { return 0; }
static double audio_stretch_max(void) { return 1.5; }
static void audio_stretch_push(const float* block, long qf, long lo, long mn, int primed) { abort(); }
void ps3_freeze_dump_now(const char* reason) { abort(); }

static const char* mock_getenv(const char* name) {
    if (strcmp(name, "AUDIO_PRIO") == 0) return "0";
    if (strcmp(name, "AUDIO_PREPAD") == 0 || strcmp(name, "AUDIO_RATE") == 0) return "1";
    if (strcmp(name, "AUDIO_ELASTIC_US") == 0) return "80000";
    return NULL;
}
#define getenv mock_getenv
'''

CHECK = r'''
int main(int argc, char** argv) {
    assert(argc == 2);
    ready_ns = low_slack_ns = stop_ns = LATE_NS + 95 * NS_PER_MS;
    if (strcmp(argv[1], "ready") == 0) ready_ns = LATE_NS + 55 * NS_PER_MS;
    if (strcmp(argv[1], "low_slack") == 0) low_slack_ns = LATE_NS + 36 * NS_PER_MS;
    if (strcmp(argv[1], "stop") == 0) stop_ns = LATE_NS + 45 * NS_PER_MS;
    audio_mix_loop_paced();
    printf("mixes=%u mixed_ns=%llu waits=%u clock_reads=%u\n",
           mixes, (unsigned long long)mixed_ns, waits, clock_reads);
    return 0;
}
'''


class AudioElasticTests(unittest.TestCase):
    def test_wait_budget_survives_clock_resyncs(self):
        compiler = shutil.which('cl') or shutil.which('clang') or shutil.which('gcc')
        if not compiler:
            self.skipTest('Run in a VS Developer Command Prompt, or put clang/gcc on PATH')
        root = Path(__file__).resolve().parents[3]
        source = (root / 'ps3recomp/libs/audio/cellAudio.c').read_text(encoding='utf-8')
        cap = re.search(r'^#define AUDIO_MAX_CATCHUP_MS .+$', source, re.MULTILINE).group()
        begin = source.index('static void audio_mix_loop_paced(void)\n{')
        end = source.index('\n}\n', begin) + 3
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            unit = directory / 'elastic.c'
            exe = directory / 'elastic.exe'
            unit.write_text(MOCK + cap + '\n' + source[begin:end] + CHECK, encoding='utf-8')
            if Path(compiler).stem.lower() == 'cl':
                args = [compiler, '/nologo', '/TC', str(unit), '/Fe:' + str(exe)]
            else:
                args = [compiler, str(unit), '-o', str(exe)]
            built = subprocess.run(args, cwd=directory, capture_output=True, text=True, timeout=30)
            self.assertEqual(built.returncode, 0, built.stdout + built.stderr)

            # Includes the initial resync caused by the jump to the start of the wait.
            for scenario, wait_ms, skips in (('timeout', 80, 4), ('ready', 55, 3), ('low_slack', 36, 2)):
                with self.subTest(scenario=scenario):
                    result = subprocess.run([str(exe), scenario], capture_output=True, text=True, timeout=5)
                    self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                    end_ns = (15000 + wait_ms) * 1000000
                    self.assertIn(
                        f'mixes=3 mixed_ns={end_ns} waits={wait_ms} clock_reads={wait_ms + 4}',
                        result.stdout,
                    )
                    self.assertIn('Mixing thread stopped', result.stdout)
                    rates = [line for line in result.stderr.splitlines() if line.startswith('[audio-rate]')]
                    self.assertEqual(len(rates), 2, result.stderr)
                    rate, next_rate = rates
                    self.assertIn(f'clock skips {skips},', rate)
                    self.assertIn(f'late guest blocks waited 1 ({wait_ms} ms),', rate)
                    self.assertTrue(rate.endswith(
                        f' start_ns=10000000000 end_ns={end_ns} max_late_wait_us={wait_ms * 1000}'
                    ), rate)
                    self.assertIn('late guest blocks waited 0 (0 ms),', next_rate)
                    self.assertTrue(next_rate.endswith(
                        f' start_ns={end_ns} end_ns={end_ns + 5000000000} max_late_wait_us=0'
                    ), next_rate)

            stopped = subprocess.run([str(exe), 'stop'], capture_output=True, text=True, timeout=5)
            self.assertEqual(stopped.returncode, 0, stopped.stdout + stopped.stderr)
            self.assertIn('mixes=1 mixed_ns=0 waits=45 clock_reads=47', stopped.stdout)
            self.assertIn('Mixing thread stopped', stopped.stdout)
            self.assertNotIn('[audio-rate]', stopped.stderr)


if __name__ == '__main__':
    unittest.main()
