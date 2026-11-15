"""Native controller regressions; VS x64 environment + clang, no physical pad needed."""
from pathlib import Path
import os
import shutil
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[3]


def function(source, name):
    begin = source.index(name)
    begin = source.rfind('\n', 0, begin) + 1
    end = source.index('\n}', begin) + 2
    return source[begin:end]


class ControllerTests(unittest.TestCase):
    def native(self, code):
        compiler = shutil.which('clang')
        if not compiler:
            candidate = Path('C:/Program Files (x86)/Microsoft Visual Studio/2022/BuildTools/VC/Tools/Llvm/bin/clang.exe')
            if candidate.exists():
                compiler = str(candidate)
        if not compiler:
            self.skipTest('clang and VS x64 SDK environment required')
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            unit, exe = directory/'controller.c', directory/'controller.exe'
            unit.write_text(code, encoding='utf-8')
            args = [compiler, '-std=c11', '-Wno-ignored-attributes',
                    '-I'+str(ROOT/'ps3recomp/include'), '-I'+str(ROOT/'sdl3/include'),
                    '-I'+str(ROOT), str(unit), '-o', str(exe), '-luser32']
            result = subprocess.run(args, cwd=directory, capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stdout+result.stderr)
            env = os.environ.copy()
            for key in list(env):
                if key.startswith('PAD_'):
                    del env[key]
            env['PAD_SDL3_DLL'] = str(ROOT/'sdl3/lib/x64/SDL3.dll')
            result = subprocess.run([str(exe)], cwd=directory, env=env,
                                    capture_output=True, text=True, timeout=30)
            self.assertEqual(result.returncode, 0, result.stdout+result.stderr)
            if 'CHECK_DISABLED_RUMBLE' in code:
                env['PAD_RUMBLE'] = '0'
                result = subprocess.run([str(exe)], cwd=directory, env=env,
                                        capture_output=True, text=True, timeout=30)
                self.assertEqual(result.returncode, 0, result.stdout+result.stderr)

    def test_assignment_overlay_rumble_and_motion_policy(self):
        source = (ROOT/'ps3recomp/libs/input/cellPad.c').read_text()
        state = source[source.index('typedef struct {'):source.index('} PadHostState;')+len('} PadHostState;')]
        overlay = (ROOT/'ps3recomp/libs/video/rsx_ui_overlay.c').read_text()
        self.native(r'''
#define WIN32_LEAN_AND_MEAN
#include <windows.h>
#include <xinput.h>
#include <assert.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include "ps3recomp/libs/input/cellPad.h"
#include "ps3recomp/libs/input/pad_sdl3.h"
#include "ps3recomp/libs/input/pad_host.h"
#define PAD_BACKEND_XINPUT 1
#define PAD_BACKEND_SDL2 0
#define PS3RECOMP_SDL3 1
#define PAD_MAX_HOST_PORTS 4
#define GUEST_PTR(p,t) (p)
#define PAD_LATCH_TAKE(p) InterlockedExchange(&latch[p],0)
''' + state + r'''
static PadHostState s_host_state[4], s_xinput_state[4];
static PadSdlPad snapshot[4];
static uint64_t s_host_assignment[4];
static SRWLOCK s_host_lock=SRWLOCK_INIT;
static volatile LONG s_overlay_input, latch[4], s_assignment_changed[4];
static int s_pad_initialized=1, g_input_kbm=0;
static DWORD rumble_slot=99; static XINPUT_VIBRATION vibration;
static uint32_t rumble_id; static unsigned rumble_small,rumble_large;
static DWORD fake_rumble(DWORD slot, XINPUT_VIBRATION* v) { rumble_slot=slot; vibration=*v; return 0; }
#define XInputSetState fake_rumble
int pad_sdl3_get(PadSdlPad* out,int max) { memcpy(out,snapshot,sizeof snapshot); return 4; }
void pad_sdl3_rumble(uint32_t id,uint8_t small,uint8_t large) { rumble_id=id;rumble_small=small;rumble_large=large; }
static void pad_note_device(int n) {}
static void pad_note_pad_type(int n) {}
''' + function(source,'static void pad_assign_controllers(void)') + '\n' +
            function(source,'unsigned cellPadGetOverlayInput(void)') + '\n' +
            function(source,'static u32 pad_assignment_change(') + '\n' + function(source,'static u32 pad_sensor_capability(') + '\n' + function(source,'s32 cellPadSetActDirect(') + r'''
enum { IN_OK=PAD_UI_OK, IN_BACK=PAD_UI_BACK, IN_LEFT=PAD_UI_LEFT, IN_RIGHT=PAD_UI_RIGHT,
       IN_TAB=PAD_UI_TAB, IN_UP=PAD_UI_UP, IN_DOWN=PAD_UI_DOWN, IN_SELECT=PAD_UI_SELECT };
volatile unsigned g_pad_file_held;
''' + function(overlay,'static unsigned read_input(HWND hwnd)') + r'''
int main(void) {
    /* CHECK_DISABLED_RUMBLE: rerun in a fresh process with PAD_RUMBLE=0. */
    if (getenv("PAD_RUMBLE")) {
        s_xinput_state[2].connected=1;pad_assign_controllers();
        CellPadActParam disabled={{1,255}};cellPadSetActDirect(0,&disabled);
        assert(rumble_slot==2 && !vibration.wLeftMotorSpeed && !vibration.wRightMotorSpeed);
        snapshot[0]=(PadSdlPad){.id=50,.connected=1};pad_assign_controllers();
        cellPadSetActDirect(1,&disabled);assert(rumble_id==50 && !rumble_small && !rumble_large);
        return 0;
    }
    /* SDL in a sparse slot controls player one; guest overlay gating cannot consume it. */
    snapshot[2]=(PadSdlPad){.id=71,.connected=1,.buttons=CELL_PAD_CTRL_CROSS|CELL_PAD_CTRL_SELECT,
        .lx=128,.ly=0,.rx=128,.ry=128};
    pad_assign_controllers();
    assert(s_host_assignment[0]==(0x100000000ull|71));
    assert(pad_assignment_change(0)==CELL_PAD_STATUS_ASSIGN_CHANGES && !pad_assignment_change(0));
    assert((read_input(NULL)&(IN_OK|IN_UP|IN_SELECT))==(IN_OK|IN_UP|IN_SELECT));
    CellPadActParam act={{1,127}};
    cellPadSetActDirect(0,&act);
    assert(rumble_id==71 && rumble_small==1 && rumble_large==127);
    /* A late Xbox in physical slot2 cannot steal logical port0. */
    s_xinput_state[2].connected=1; s_xinput_state[2].analog_lx=s_xinput_state[2].analog_ly=128;
    pad_assign_controllers();
    assert(s_host_assignment[0]==(0x100000000ull|71) && s_host_assignment[1]==3);
    cellPadSetActDirect(1,&act);
    assert(rumble_slot==2 && vibration.wRightMotorSpeed==65535 && vibration.wLeftMotorSpeed==32639);
    act.motor[0]=0;act.motor[1]=0;cellPadSetActDirect(1,&act);
    assert(!vibration.wRightMotorSpeed && !vibration.wLeftMotorSpeed);
    /* Disconnect promotes the existing Xbox, clears latched input, and reconnect cannot steal it. */
    snapshot[2].connected=0; latch[0]=CELL_PAD_CTRL_CROSS;
    pad_assign_controllers();
    assert(s_host_assignment[0]==3 && !latch[0] && !s_host_state[0].buttons);
    snapshot[2].connected=1;snapshot[2].id=72;
    pad_assign_controllers();
    assert(s_host_assignment[0]==3 && s_host_assignment[1]==(0x100000000ull|72));
    snapshot[2].motion=1;snapshot[2].sensors[0]=600;pad_assign_controllers();
    assert(pad_sensor_capability(1)==CELL_PAD_CAPABILITY_SENSOR_MODE);
    assert(s_host_state[1].sensors[0]==600 && pad_sensor_capability(0)==0);
    assert(pad_host_small_motor(255)==65535);
    uint16_t sensor[4]; float a[3]={0,9.80665f,0},g[3]={0,1.57079632679f,0};
    pad_host_motion(sensor,a,g);
    assert(sensor[0]==512 && sensor[1]==399 && sensor[2]==512 && sensor[3]==635);
    a[0]=1000;a[1]=-1000;a[2]=NAN;g[1]=INFINITY;
    pad_host_motion(sensor,a,g);
    assert(sensor[0]==0 && sensor[1]==1023 && sensor[2]==512 && sensor[3]==512);
    pad_host_motion(sensor,NULL,NULL);assert(sensor[1]==399 && sensor[3]==512);
    return 0;
}
''')

    def test_sdl_hotplug_sensor_sampling_and_rumble_identity(self):
        self.native(r'''
#define PS3RECOMP_SDL3 1
#include "ps3recomp/libs/input/pad_sdl3.c"
#include <assert.h>
struct SDL_Gamepad { int slot; };
static struct SDL_Gamepad pads[2]={{0},{1}};
static int alive[2]={1,1}; static unsigned motor[2][2];
static int sensor_success=1;
static const char* name(SDL_Gamepad* g) { return "mock"; }
static void close_gp(SDL_Gamepad* g) {}
static bool connected(SDL_Gamepad* g) { return alive[g->slot]!=0; }
static SDL_GamepadType type(SDL_Gamepad* g) { return SDL_GAMEPAD_TYPE_PS5; }
static bool button(SDL_Gamepad* g,SDL_GamepadButton b) { return b==SDL_GAMEPAD_BUTTON_SOUTH || b==SDL_GAMEPAD_BUTTON_TOUCHPAD; }
static Sint16 axis(SDL_Gamepad* g,SDL_GamepadAxis a) { return a==SDL_GAMEPAD_AXIS_LEFT_TRIGGER ? 32767 : 0; }
static void update(void) {}
static bool sensor(SDL_Gamepad* g,SDL_SensorType t,float* data,int count) {
    if (!sensor_success) return false;
    data[0]=0;data[1]=t==SDL_SENSOR_ACCEL ? 9.80665f : -1.57079632679f;data[2]=0;return true;
}
static bool rumble(SDL_Gamepad* g,Uint16 low,Uint16 high,Uint32 ms) {motor[g->slot][0]=low;motor[g->slot][1]=high;return true;}
static bool led(SDL_Gamepad* g,Uint8 r,Uint8 green,Uint8 b) {return true;}
static SDL_JoystickID* gamepads(int* count) {*count=0;return NULL;}
int main(void) {
    InitializeCriticalSection(&s_lock);s_state=1;s_nopen=2;
    p_SDL_GetGamepadName=name;p_SDL_CloseGamepad=close_gp;p_SDL_GamepadConnected=connected;
    p_SDL_GetGamepadType=type;p_SDL_GetGamepadButton=button;p_SDL_GetGamepadAxis=axis;
    p_SDL_UpdateGamepads=update;p_SDL_GetGamepadSensorData=sensor;
    p_SDL_RumbleGamepad=rumble;p_SDL_SetGamepadLED=led;p_SDL_GetGamepads=gamepads;
    s_open[0]=(OpenPad){.gp=&pads[0],.id=10,.accel=1,.gyro=1,.accel_type=SDL_SENSOR_ACCEL,.gyro_type=SDL_SENSOR_GYRO};
    s_open[1]=(OpenPad){.gp=&pads[1],.id=20,.accel=1,.gyro=1,.accel_type=SDL_SENSOR_ACCEL,.gyro_type=SDL_SENSOR_GYRO};
    pad_sdl3_rumble(20,1,64);pad_sdl3_update();
    PadSdlPad out[4];assert(pad_sdl3_get(out,4)==4);
    assert(out[1].connected && out[1].id==20 && out[1].motion);
    assert(out[1].sensors[1]==399 && out[1].sensors[3]==389);
    sensor_success=0;pad_sdl3_update();pad_sdl3_get(out,4);
    assert(out[1].sensors[1]==399 && out[1].sensors[3]==512); /* no stale motion */
    assert(out[1].buttons & CELL_PAD_CTRL_SELECT);assert(out[1].l2==255);
    assert(motor[1][0]==16448 && motor[1][1]==65535 && !motor[0][1]);
    alive[0]=0;pad_sdl3_update();pad_sdl3_get(out,4);
    assert(!out[0].connected && out[1].id==20); /* no compaction */
    pad_sdl3_rumble(10,1,255);assert(s_rumble_req[0]==0); /* stale connection */
    pad_sdl3_rumble(20,0,0);pad_sdl3_update();assert(!motor[1][0] && !motor[1][1]);
    pad_sdl3_rumble(20,1,255);pad_sdl3_update();pad_sdl3_stop_rumble();
    assert(!motor[1][0] && !motor[1][1] && !s_nopen);pad_sdl3_get(out,4);
    assert(!s_nsnap);
    return 0;
}
''')

    def test_real_sdl_virtual_controller(self):
        self.native(r'''
#define PS3RECOMP_SDL3 1
#include "ps3recomp/libs/input/pad_sdl3.c"
#include <assert.h>
static unsigned low,high;static bool sensors_on;
static bool SDLCALL rumble(void* u,Uint16 l,Uint16 h) {low=l;high=h;return true;}
static bool SDLCALL sensors(void* u,bool enabled) {sensors_on=enabled;return true;}
#define GET(n) __typeof__(n)* f_##n=(__typeof__(n)*)(void*)GetProcAddress(dll,#n);assert(f_##n)
int main(void) {
    pad_sdl3_update();assert(s_state==1);
    HMODULE dll=load_sdl();assert(dll);
    GET(SDL_AttachVirtualJoystick);GET(SDL_DetachVirtualJoystick);
    GET(SDL_GetGamepadJoystick);GET(SDL_SetJoystickVirtualButton);
    GET(SDL_SetJoystickVirtualAxis);GET(SDL_SendJoystickVirtualSensorData);
    SDL_VirtualJoystickSensorDesc sensor_desc[2]={{SDL_SENSOR_ACCEL,250},{SDL_SENSOR_GYRO,250}};
    SDL_VirtualJoystickDesc desc={0};desc.version=sizeof desc;
    desc.type=SDL_JOYSTICK_TYPE_GAMEPAD;desc.name="Recomp regression virtual pad";
    desc.naxes=6;desc.nbuttons=16;desc.axis_mask=0x3f;desc.button_mask=0xffff;
    desc.nsensors=2;desc.sensors=sensor_desc;desc.SetSensorsEnabled=sensors;desc.Rumble=rumble;
    SDL_JoystickID id=f_SDL_AttachVirtualJoystick(&desc);assert(id);
    p_SDL_UpdateGamepads();rescan();pad_sdl3_update();
    int slot=-1;for(int i=0;i<4;i++) if(s_open[i].id==id) slot=i;
    assert(slot>=0 && sensors_on);
    SDL_Joystick* joy=f_SDL_GetGamepadJoystick(s_open[slot].gp);assert(joy);
    assert(f_SDL_SetJoystickVirtualButton(joy,SDL_GAMEPAD_BUTTON_SOUTH,true));
    assert(f_SDL_SetJoystickVirtualAxis(joy,SDL_GAMEPAD_AXIS_LEFTX,32767));
    assert(f_SDL_SetJoystickVirtualAxis(joy,SDL_GAMEPAD_AXIS_LEFT_TRIGGER,32767));
    float accel[3]={0,9.80665f,0},gyro[3]={0,1.57079632679f,0};
    assert(f_SDL_SendJoystickVirtualSensorData(joy,SDL_SENSOR_ACCEL,1,accel,3));
    assert(f_SDL_SendJoystickVirtualSensorData(joy,SDL_SENSOR_GYRO,1,gyro,3));
    pad_sdl3_update();PadSdlPad out[4];pad_sdl3_get(out,4);
    assert(out[slot].buttons & CELL_PAD_CTRL_CROSS);
    assert(out[slot].lx==255 && out[slot].l2==255);
    assert(out[slot].motion && out[slot].sensors[1]==399 && out[slot].sensors[3]==635);
    pad_sdl3_rumble(id,1,128);pad_sdl3_update();
    assert(low==32896 && high==65535);
    pad_sdl3_rumble(id,0,0);pad_sdl3_update();assert(!low && !high);
    assert(f_SDL_DetachVirtualJoystick(id));pad_sdl3_update();pad_sdl3_get(out,4);
    assert(!out[slot].connected);
    pad_sdl3_stop_rumble();return 0;
}
''')


if __name__ == '__main__':
    unittest.main()
