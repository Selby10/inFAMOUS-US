#ifndef RSX_WINDOW_SIZE_H
#define RSX_WINDOW_SIZE_H
#include <stdint.h>
#include <string.h>

typedef enum {
    RSX_WINDOW_AUTO,
    RSX_WINDOW_720P,
    RSX_WINDOW_1080P,
    RSX_WINDOW_PRESET_COUNT
} rsx_window_preset;

/* Private game-window message. WPARAM carries an rsx_window_preset by value. */
#define RSX_WM_WINDOW_SIZE (WM_APP + 0x2A1)

static inline rsx_window_preset rsx_window_preset_from_setting(const char* setting)
{
    if (setting && !strcmp(setting, "720p")) return RSX_WINDOW_720P;
    if (setting && !strcmp(setting, "1080p")) return RSX_WINDOW_1080P;
    return RSX_WINDOW_AUTO;
}

/* Presentation size is independent of the render targets/swap-chain buffers.
 * Unknown/absent settings preserve the existing match-render-resolution mode. */
static inline void rsx_window_dimensions(rsx_window_preset preset, uint32_t render_w,
                                        uint32_t render_h, uint32_t* w, uint32_t* h)
{
    *w = render_w;
    *h = render_h;
    if (preset == RSX_WINDOW_720P) { *w = 1280; *h = 720; }
    else if (preset == RSX_WINDOW_1080P) { *w = 1920; *h = 1080; }
}
#endif
