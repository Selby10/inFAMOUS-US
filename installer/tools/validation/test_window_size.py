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
#include <math.h>
#include <assert.h>
#include "rsx_window_size.h"
typedef uint32_t u32;
#define BBW 640
#define BBH 360
volatile int g_win_fullscreen, g_win_sizemove, g_uiov_show_fps, g_pad_mouse_captured, g_pad_raw_ok;
volatile long g_pad_raw_mdx, g_pad_raw_mdy, g_pad_raw_wheel;
static struct { int window_closed; } s_d3d;
void uiov_queue_shortcut(UINT key) { (void)key; }
static HWND hidden_window(DWORD ex, LPCSTR cls, LPCSTR title, DWORD style,
    int x,int y,int w,int h,HWND parent,HMENU menu,HINSTANCE app,LPVOID param) {
    return CreateWindowExA(ex,cls,title,style & ~WS_VISIBLE,x,y,w,h,parent,menu,app,param);
}
static BOOL position_hidden_window(HWND hwnd, HWND after, int x, int y, int w, int h, UINT flags) {
    if (flags & SWP_FRAMECHANGED) flags = (flags & ~SWP_SHOWWINDOW) | SWP_NOACTIVATE;
    else assert(flags & SWP_NOACTIVATE);
    return SetWindowPos(hwnd,after,x,y,w,h,flags);
}
#define CreateWindowExA hidden_window
#define SetWindowPos position_hidden_window
''' + backend.split(' * Win32 window\n * -----------------------------------------------------------------------*/\n', 1)[1].split(
            '/* ---------------------------------------------------------------------------', 1)[0] + r'''
#undef CreateWindowExA
#undef SetWindowPos
''' + function(main, 'static void load_settings_ini(void)') + r'''
static void post(HWND hwnd, UINT message, WPARAM value) {
    MSG msg;
    assert(PostMessageA(hwnd,message,value,0));
    while (PeekMessageA(&msg,NULL,0,0,PM_REMOVE)) DispatchMessageA(&msg);
}

static RECT fitted_window(HWND hwnd, RECT area) {
    RECT client,outer;
    assert(GetClientRect(hwnd,&client)); assert(GetWindowRect(hwnd,&outer));
    assert(client.right>0 && client.bottom>0);
    assert(outer.left>=area.left && outer.right<=area.right);
    assert(outer.top>=area.top && outer.bottom<=area.bottom);
    assert(abs(client.right*9-client.bottom*16)<=16);
    return outer;
}

int main(void) {
    SetProcessDPIAware();
    u32 w,h;
    rsx_window_dimensions(rsx_window_preset_from_setting(NULL),1920,1080,&w,&h); assert(w==1920 && h==1080);
    rsx_window_dimensions(rsx_window_preset_from_setting("invalid"),1280,720,&w,&h); assert(w==1280 && h==720);
    FILE* f=fopen("settings.ini","w");
    fprintf(f,"WINDOW_SIZE=1080p\nRES_SCALE=1\n"); fclose(f);
    load_settings_ini();
    assert(!strcmp(getenv("WINDOW_SIZE"),"1080p"));
    assert(!strcmp(getenv("RES_SCALE"),"1"));
    const char* modes[]={"720p","1080p"};
    RECT area; assert(SystemParametersInfoA(SPI_GETWORKAREA,0,&area,0));
    for(int i=0;i<2;i++) {
        rsx_window_dimensions(rsx_window_preset_from_setting(modes[i]),1280,720,&w,&h);
        assert(w==(i ? 1920:1280) && h==(i ? 1080:720));
        HWND hwnd=create_window(w,h,"Window size regression"); assert(hwnd);
        RECT client,outer; assert(GetClientRect(hwnd,&client)); assert(GetWindowRect(hwnd,&outer));
        assert(client.right>0 && client.bottom>0);
        assert(outer.right-outer.left<=area.right-area.left);
        assert(outer.bottom-outer.top<=area.bottom-area.top);
        assert(abs(client.right*9-client.bottom*16)<=16);
        DestroyWindow(hwnd);
    }
    HWND hwnd=create_window(1280,720,"Live window size regression"); assert(hwnd);
    MONITORINFO monitor={sizeof(monitor)};
    assert(GetMonitorInfoA(MonitorFromWindow(hwnd,MONITOR_DEFAULTTONEAREST),&monitor));
    area=monitor.rcWork;
    assert(SetWindowPos(hwnd,NULL,area.right-120,area.bottom-80,120,80,SWP_NOZORDER|SWP_NOACTIVATE));
    post(hwnd,RSX_WM_WINDOW_SIZE,RSX_WINDOW_720P);
    RECT windowed=fitted_window(hwnd,area);
    assert(windowed.right-windowed.left>120 && windowed.bottom-windowed.top>80);
    post(hwnd,RSX_WM_WINDOW_SIZE,(WPARAM)-1);
    RECT unchanged; assert(GetWindowRect(hwnd,&unchanged)); assert(EqualRect(&windowed,&unchanged));
    post(hwnd,WM_KEYDOWN,VK_F11);
    RECT fullscreen; assert(GetWindowRect(hwnd,&fullscreen)); assert(EqualRect(&fullscreen,&monitor.rcMonitor));
    post(hwnd,WM_KEYDOWN,VK_F11);
    assert(GetWindowRect(hwnd,&unchanged)); assert(EqualRect(&windowed,&unchanged));
    assert(SetWindowPos(hwnd,NULL,area.left,area.top,120,80,SWP_NOZORDER|SWP_NOACTIVATE));
    post(hwnd,WM_KEYDOWN,VK_F11);
    post(hwnd,RSX_WM_WINDOW_SIZE,RSX_WINDOW_AUTO);
    post(hwnd,RSX_WM_WINDOW_SIZE,RSX_WINDOW_1080P);
    assert(GetWindowRect(hwnd,&unchanged)); assert(EqualRect(&fullscreen,&unchanged));
    post(hwnd,WM_KEYDOWN,VK_F11);
    RECT restored=fitted_window(hwnd,area);
    assert(restored.right-restored.left>=windowed.right-windowed.left);
    assert(restored.bottom-restored.top>=windowed.bottom-windowed.top);
    DestroyWindow(hwnd);
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
