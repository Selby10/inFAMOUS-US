"""Exercise the production checksum worker's buffer lifetime with real Windows threads."""
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest


class TextureJobTests(unittest.TestCase):
    def test_worker_excludes_buffer_reuse_and_rejects_stale_generation(self):
        compiler = shutil.which('cl')
        if not compiler:
            self.skipTest('VS x64 Developer Command Prompt required')
        root = Path(__file__).resolve().parents[3]
        source = (root/'ps3recomp/libs/video/rsx_d3d12_backend.c').read_text()
        def extract(name):
            begin = source.rfind('\n', 0, source.index(name)) + 1
            return source[begin:source.index('\n}', begin)+2]
        unit = r'''
#include <windows.h>
#include <assert.h>
#include <stdint.h>
typedef uint32_t u32; typedef uint64_t u64;
typedef struct { u32 value; volatile LONG64 res; } TexCsJob;
static TexCsJob s_tcj[2][1];
static volatile LONG64 s_tcj_ctr;
static volatile LONG s_tcj_wake, s_tcj_in[2];
static u64 s_tcs_byw;
static SRWLOCK s_tcj_lock[2]={SRWLOCK_INIT,SRWLOCK_INIT};
static HANDLE entered,finish;
static u32 tcs_compute(const TexCsJob* job) {
    SetEvent(entered);assert(WaitForSingleObject(finish,5000)==WAIT_OBJECT_0);
    return job->value;
}
static BOOL fake_wait(volatile VOID* a,PVOID b,SIZE_T n,DWORD ms) {ExitThread(0);return TRUE;}
#define WaitOnAddress fake_wait
''' + extract('static int tcs_claim(') + '\n' + extract('static void tcs_run(') + '\n' + extract('static DWORD WINAPI tcs_worker(') + r'''
int main(void) {
    entered=CreateEventA(NULL,TRUE,FALSE,NULL);finish=CreateEventA(NULL,TRUE,FALSE,NULL);
    s_tcj_wake=1;s_tcj_ctr=(1ull<<40)|(1ull<<20);s_tcj[1][0].value=123;
    HANDLE thread=CreateThread(NULL,0,tcs_worker,NULL,0,NULL);assert(thread);
    assert(WaitForSingleObject(entered,5000)==WAIT_OBJECT_0);
    /* A real worker is paused inside the checksum: writer must fall back,
     * without mutating its generation's job buffer or waiting for it. */
    assert(!TryAcquireSRWLockExclusive(&s_tcj_lock[1]));
    SetEvent(finish);assert(WaitForSingleObject(thread,5000)==WAIT_OBJECT_0);
    assert(s_tcj[1][0].res==((1ull<<32)|123));
    assert(TryAcquireSRWLockExclusive(&s_tcj_lock[1]));
    s_tcj_ctr=(3ull<<40)|(1ull<<20);s_tcj[1][0].value=456;s_tcj[1][0].res=0;
    ReleaseSRWLockExclusive(&s_tcj_lock[1]);
    /* A delayed generation1 worker cannot claim generation3's jobs. */
    assert(tcs_claim(1)==-1);assert(s_tcj[1][0].res==0);
    CloseHandle(thread);CloseHandle(entered);CloseHandle(finish);return 0;
}
'''
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp); path = folder/'jobs.c'; exe=folder/'jobs.exe'
            path.write_text(unit)
            built=subprocess.run([compiler,'/nologo','/TC',str(path),'/Fe:'+str(exe)],cwd=folder,capture_output=True,text=True)
            self.assertEqual(built.returncode,0,built.stdout+built.stderr)
            ran=subprocess.run([str(exe)],cwd=folder,capture_output=True,text=True,timeout=15)
            self.assertEqual(ran.returncode,0,ran.stdout+ran.stderr)


if __name__=='__main__':
    unittest.main()
