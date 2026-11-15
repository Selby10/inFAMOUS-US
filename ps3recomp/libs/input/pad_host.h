/* Host input policy, independent of Windows/SDL so it can be tested without hardware. */
#ifndef PS3RECOMP_PAD_HOST_H
#define PS3RECOMP_PAD_HOST_H
#include <stdint.h>
#include <math.h>

enum { PAD_UI_OK=1, PAD_UI_BACK=2, PAD_UI_LEFT=4, PAD_UI_RIGHT=8,
       PAD_UI_TAB=16, PAD_UI_UP=32, PAD_UI_DOWN=64, PAD_UI_SELECT=128 };
static inline unsigned pad_host_ui(uint16_t b, unsigned x, unsigned y)
{
    return ((b & 0x4000) ? PAD_UI_OK : 0) | ((b & 0x2000) ? PAD_UI_BACK : 0) |
        (((b & 0x0080) || x < 66) ? PAD_UI_LEFT : 0) |
        (((b & 0x0020) || x > 190) ? PAD_UI_RIGHT : 0) |
        (((b & 0x0010) || y < 66) ? PAD_UI_UP : 0) |
        (((b & 0x0040) || y > 190) ? PAD_UI_DOWN : 0) |
        ((b & 1) ? PAD_UI_SELECT : 0);
}
static inline uint16_t pad_host_small_motor(unsigned value) { return value ? 65535 : 0; }
static inline uint16_t pad_host_sensor(float value, unsigned fallback)
{
    if (!isfinite(value)) return (uint16_t)fallback;
    return (uint16_t)(value < 0 ? 0 : value > 1023 ? 1023 : value + 0.5f);
}
/* SDL SI units -> DS3: accel 113 counts/g (opposite axes), yaw 123 counts/90deg/s.
 * See SDL_sensor.h axis conventions and RPCS3 Input/sdl_pad_handler.cpp. */
static inline void pad_host_motion(uint16_t out[4], const float* accel, const float* gyro)
{
    out[0]=512; out[1]=399; out[2]=512; out[3]=512;
    if (accel) for (int i=0; i<3; i++)
        out[i]=pad_host_sensor(512.0f - accel[i] * (113.0f / 9.80665f), out[i]);
    if (gyro) out[3]=pad_host_sensor(512.0f + gyro[1] * (246.0f / 3.14159265358979323846f), 512);
}
/* Zero = empty. Preserve live identities; late connections cannot steal a port.
 * When player one disconnects, promote an existing controller before newcomers. */
static inline void pad_host_assign(uint64_t ports[4], const uint64_t* live, int count)
{
    for (int p=0; p<4; p++) {
        int found=0;
        for (int i=0; i<count; i++) if (ports[p] && ports[p]==live[i]) found=1;
        if (!found) ports[p]=0;
    }
    if (!ports[0]) for (int p=1; p<4; p++) if (ports[p]) {
        ports[0]=ports[p]; ports[p]=0; break;
    }
    for (int i=0; i<count; i++) {
        int found=0;
        for (int p=0; p<4; p++) if (ports[p]==live[i]) found=1;
        if (!live[i] || found) continue;
        for (int p=0; p<4; p++) if (!ports[p]) { ports[p]=live[i]; break; }
    }
}
#endif
