"""Verify slow host CPU reporting cannot block a guest telemetry request."""
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest


class TelemetryTests(unittest.TestCase):
    def test_host_report_runs_off_caller_and_bounds_pending_work(self):
        compiler = shutil.which('cl')
        if not compiler:
            self.skipTest('VS x64 Developer Command Prompt required')
        root = Path(__file__).resolve().parents[3]
        source = (root/'ps3recomp/runtime/ppu/ppu_loader.cpp').read_text()
        begin = source.index('static INIT_ONCE s_telem_cpu_once')
        end = source.index('\n#endif', begin)
        unit = r'''
#include <windows.h>
#include <assert.h>
static HANDLE entered, finish;
static volatile LONG reports;
static DWORD caller;
static void telem_cpu_report(double ms) {
    assert(GetCurrentThreadId()!=caller);assert(ms==10000.0);
    InterlockedIncrement(&reports);SetEvent(entered);
    assert(WaitForSingleObject(finish,5000)==WAIT_OBJECT_0);
}
''' + source[begin:end] + r'''
int main() {
    caller=GetCurrentThreadId();
    entered=CreateEventA(NULL,FALSE,FALSE,NULL);finish=CreateEventA(NULL,TRUE,FALSE,NULL);
    /* The report deliberately blocks: request must return before release. */
    telem_cpu_request(10000.0);
    assert(WaitForSingleObject(entered,5000)==WAIT_OBJECT_0);
    assert(reports==1);
    for(int i=0;i<100;i++) telem_cpu_request(10000.0);
    assert(reports==1);
    SetEvent(finish);
    ULONGLONG limit=GetTickCount64()+5000;
    while(InterlockedCompareExchange64(&s_telem_cpu_window_us,0,0)!=0 && GetTickCount64()<limit) Sleep(1);
    assert(s_telem_cpu_window_us==0);
    assert(WaitForSingleObject(entered,20)==WAIT_TIMEOUT);
    telem_cpu_request(10000.0);
    assert(WaitForSingleObject(entered,5000)==WAIT_OBJECT_0);assert(reports==2);
    return 0;
}
'''
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp); path = folder/'telem.cpp'; exe = folder/'telem.exe'
            path.write_text(unit)
            built = subprocess.run([compiler, '/nologo', str(path), '/Fe:'+str(exe)],
                                   cwd=folder, capture_output=True, text=True)
            self.assertEqual(built.returncode, 0, built.stdout+built.stderr)
            ran = subprocess.run([str(exe)], cwd=folder, capture_output=True, text=True, timeout=15)
            self.assertEqual(ran.returncode, 0, ran.stdout+ran.stderr)


if __name__ == '__main__':
    unittest.main()
