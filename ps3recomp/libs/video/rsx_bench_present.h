#pragma once

#include <stddef.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>
#include <windows.h>

/* Versioned little-endian layout consumed by analyze_host_frames.py. The render path
 * has one writer, including when frame execution is handed to the async thread. */
enum {
    RSX_BENCH_PRESENT_CAPACITY = 1 << 20,
    RSX_BENCH_PRESENT_FULL = 1,
    RSX_BENCH_PRESENT_CLOCK_ERROR = 2,
    RSX_BENCH_PRESENT_INCLUDE_BACKGROUND = 4
};

typedef struct {
    uint64_t qpc;
    uint64_t attempt;
} rsx_bench_present_sample;

typedef struct {
    char magic[8];
    uint64_t frequency;
    uint64_t origin_qpc;
    uint64_t origin_filetime;
    uint32_t capacity;
    volatile LONG committed;
    volatile LONG flags;
    uint32_t pid;
    rsx_bench_present_sample samples[RSX_BENCH_PRESENT_CAPACITY];
} rsx_bench_present_buffer;

_Static_assert(offsetof(rsx_bench_present_buffer, samples) == 48, "Host Present capture header layout");
_Static_assert(sizeof(rsx_bench_present_sample) == 16, "Host Present capture sample layout");

static rsx_bench_present_buffer* s_rsx_bench_present;
static uint64_t s_rsx_bench_present_attempt;

static void rsx_bench_present_init(void)
{
    static int initialized;
    if (initialized) return;
    initialized = 1;

    WCHAR path[32768];
    const DWORD length = GetEnvironmentVariableW(L"INFAMOUS_HOST_PRESENT_FILE", path, ARRAYSIZE(path));
    if (!length) return;
    if (length >= ARRAYSIZE(path)) {
        fprintf(stderr, "[HOST_PRESENT] capture path too long\n");
        return;
    }

    LARGE_INTEGER frequency, origin;
    if (!QueryPerformanceFrequency(&frequency) || !QueryPerformanceCounter(&origin)) {
        fprintf(stderr, "[HOST_PRESENT] could not initialize the performance counter\n");
        return;
    }
    FILETIME utc;
    GetSystemTimePreciseAsFileTime(&utc);

    HANDLE file = CreateFileW(path, GENERIC_READ | GENERIC_WRITE, FILE_SHARE_READ, NULL,
                              CREATE_NEW, FILE_ATTRIBUTE_NORMAL, NULL);
    if (file == INVALID_HANDLE_VALUE) {
        fprintf(stderr, "[HOST_PRESENT] could not create capture file (Win32 %lu)\n", GetLastError());
        return;
    }
    HANDLE mapping = CreateFileMappingW(file, NULL, PAGE_READWRITE, 0, sizeof(rsx_bench_present_buffer), NULL);
    if (!mapping) {
        fprintf(stderr, "[HOST_PRESENT] could not create capture mapping (Win32 %lu)\n", GetLastError());
        CloseHandle(file);
        return;
    }
    rsx_bench_present_buffer* buffer = MapViewOfFile(mapping, FILE_MAP_WRITE, 0, 0, 0);
    if (!buffer) fprintf(stderr, "[HOST_PRESENT] could not map the capture file (Win32 %lu)\n", GetLastError());
    CloseHandle(mapping);
    CloseHandle(file);
    if (!buffer) return;

    buffer->frequency = (uint64_t)frequency.QuadPart;
    buffer->origin_qpc = (uint64_t)origin.QuadPart;
    buffer->origin_filetime = ((uint64_t)utc.dwHighDateTime << 32) | utc.dwLowDateTime;
    buffer->capacity = RSX_BENCH_PRESENT_CAPACITY;
    buffer->committed = 0;
    WCHAR include_background[2];
    buffer->flags = (GetEnvironmentVariableW(L"INFAMOUS_HOST_PRESENT_INCLUDE_BACKGROUND", include_background,
                                             ARRAYSIZE(include_background)) == 1 && include_background[0] == L'1')
        ? RSX_BENCH_PRESENT_INCLUDE_BACKGROUND : 0;
    buffer->pid = GetCurrentProcessId();
    memcpy(buffer->magic, "INFPRES1", sizeof buffer->magic);
    /* Keep the file-backed view through process teardown, including ExitProcess.
     * The collector opens a read-only view of the same file after the child exits. */
    s_rsx_bench_present = buffer;
}

static inline LONGLONG rsx_bench_present_begin(HWND hwnd)
{
    ++s_rsx_bench_present_attempt;
    /* Flip-model DXGI may return S_OK even while the window has no visible output. */
    if (IsIconic(hwnd) || !IsWindowVisible(hwnd)) return 0;
    if (!(s_rsx_bench_present->flags & RSX_BENCH_PRESENT_INCLUDE_BACKGROUND) && GetForegroundWindow() != hwnd)
        return 0;

    LARGE_INTEGER now;
    if (QueryPerformanceCounter(&now)) return now.QuadPart;

    InterlockedOr(&s_rsx_bench_present->flags, RSX_BENCH_PRESENT_CLOCK_ERROR);
    s_rsx_bench_present = NULL;
    fprintf(stderr, "[HOST_PRESENT] performance counter failed, capture stopped\n");
    return 0;
}

static inline void rsx_bench_present_end(LONGLONG qpc, HRESULT result)
{
    if (!qpc || result != S_OK) return;
    rsx_bench_present_buffer* buffer = s_rsx_bench_present;
    const LONG index = buffer->committed;
    if ((uint32_t)index == buffer->capacity) {
        InterlockedOr(&buffer->flags, RSX_BENCH_PRESENT_FULL);
        s_rsx_bench_present = NULL;
        fprintf(stderr, "[HOST_PRESENT] capture capacity exhausted, capture stopped\n");
        return;
    }
    buffer->samples[index] = (rsx_bench_present_sample){ (uint64_t)qpc, s_rsx_bench_present_attempt };
    /* Commit only after publishing a complete entry. An interrupted write leaves a usable prefix. */
    InterlockedExchange(&buffer->committed, index + 1);
}
