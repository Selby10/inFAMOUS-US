"""Real Win32 window fitting and settings loading, without a game or GPU."""
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[3]


def function(source, signature):
    start = source.index(signature + '\n{')
    return source[start:source.index('\n}', start) + 3]


class WindowSizeTests(unittest.TestCase):
    def test_window_selection_preserves_render_size_and_fits_desktop(self):
        compiler = shutil.which('cl')
        if not compiler:
            self.skipTest('VS x64 environment required')
        backend = (ROOT/'ps3recomp/libs/video/rsx_d3d12_backend.c').read_text()
        main = (ROOT/'infamous-recomp/main.cpp').read_text()
        code = r'''
#include <windows.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <assert.h>
#include "rsx_window_size.h"
typedef uint32_t u32;
#define d3d12_wndproc DefWindowProcA
static HWND hidden_window(DWORD ex, LPCSTR cls, LPCSTR title, DWORD style,
    int x,int y,int w,int h,HWND parent,HMENU menu,HINSTANCE app,LPVOID param) {
    return CreateWindowExA(ex,cls,title,style & ~WS_VISIBLE,x,y,w,h,parent,menu,app,param);
}
#define CreateWindowExA hidden_window
''' + function(backend, 'static HWND create_window(u32 width, u32 height, const char* title)') + r'''
#undef CreateWindowExA
''' + function(main, 'static void load_settings_ini(void)') + r'''
int main(void) {
    SetProcessDPIAware();
    u32 w,h;
    rsx_window_dimensions(NULL,1920,1080,&w,&h); assert(w==1920 && h==1080);
    rsx_window_dimensions("invalid",1280,720,&w,&h); assert(w==1280 && h==720);
    FILE* f=fopen("settings.ini","w");
    fprintf(f,"WINDOW_SIZE=1080p\nRES_SCALE=1\n"); fclose(f);
    load_settings_ini();
    assert(!strcmp(getenv("WINDOW_SIZE"),"1080p"));
    assert(!strcmp(getenv("RES_SCALE"),"1"));
    const char* modes[]={"720p","1080p"};
    RECT area; assert(SystemParametersInfoA(SPI_GETWORKAREA,0,&area,0));
    for(int i=0;i<2;i++) {
        rsx_window_dimensions(modes[i],1280,720,&w,&h);
        assert(w==(i ? 1920:1280) && h==(i ? 1080:720));
        HWND hwnd=create_window(w,h,"Window size regression"); assert(hwnd);
        RECT client,outer; assert(GetClientRect(hwnd,&client)); assert(GetWindowRect(hwnd,&outer));
        assert(client.right>0 && client.bottom>0);
        assert(outer.right-outer.left<=area.right-area.left);
        assert(outer.bottom-outer.top<=area.bottom-area.top);
        assert(abs(client.right*9-client.bottom*16)<=16);
        DestroyWindow(hwnd);
    }
    return 0;
}
'''
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            (directory/'window.c').write_text(code)
            built = subprocess.run([compiler, '/nologo', '/TC', '/I'+str(ROOT/'ps3recomp/libs/video'),
                                    'window.c', '/Fe:window.exe', '/link', 'user32.lib'], cwd=directory,
                                   capture_output=True, text=True)
            self.assertEqual(built.returncode, 0, built.stdout+built.stderr)
            result = subprocess.run([str(directory/'window.exe')], cwd=directory,
                                    capture_output=True, text=True, timeout=15)
            self.assertEqual(result.returncode, 0, result.stdout+result.stderr)
