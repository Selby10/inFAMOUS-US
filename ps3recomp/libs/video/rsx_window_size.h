#ifndef RSX_WINDOW_SIZE_H
#define RSX_WINDOW_SIZE_H
#include <stdint.h>
#include <string.h>

/* Presentation size is independent of the render targets/swap-chain buffers.
 * Unknown/absent settings preserve the existing match-render-resolution mode. */
static inline void rsx_window_dimensions(const char* setting, uint32_t render_w,
                                        uint32_t render_h, uint32_t* w, uint32_t* h)
{
    *w = render_w;
    *h = render_h;
    if (setting && !strcmp(setting, "720p")) { *w = 1280; *h = 720; }
    else if (setting && !strcmp(setting, "1080p")) { *w = 1920; *h = 1080; }
}
#endif
