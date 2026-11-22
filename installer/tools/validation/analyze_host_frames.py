"""Summarize host-supplied DXGI Present intervals from a completed game's capture file."""
import argparse
import datetime as dt
import json
import math
import mmap
import os
import struct

from analyze_frames import summarize


# File layout from ps3recomp/libs/video/rsx_bench_present.h.
HEADER = struct.Struct('<8sQQQIIII')
SAMPLE = struct.Struct('<QQ')
CAPACITY = 1 << 20
FLAG_FULL = 1
FLAG_CLOCK_ERROR = 2
FLAG_INCLUDE_BACKGROUND = 4
FOREGROUND_SCOPE = 'foreground_window'
BACKGROUND_SCOPE = 'visible_window_including_background'


def validate_range(start, end):
    if not math.isfinite(start) or start < 0 or (end is not None and
                                               (not math.isfinite(end) or end <= start)):
        raise ValueError('Require a nonnegative start and a finite end greater than start, if supplied')


def analyze_host_frames(path, start=0, end=None, pid=None):
    validate_range(start, end)
    with open(path, 'rb') as stream:
        size = os.fstat(stream.fileno()).st_size
        if not HEADER.size <= size <= HEADER.size + CAPACITY * SAMPLE.size:
            raise ValueError('Host Present capture file is incomplete or the wrong size')
        with mmap.mmap(stream.fileno(), 0, access=mmap.ACCESS_READ) as capture:
            return analyze_capture(capture, start, end, pid)


def analyze_capture(capture, start, end, pid):
    magic, frequency, origin, filetime, capacity, count, flags, sample_pid = HEADER.unpack_from(capture)
    if (magic != b'INFPRES1' or not frequency or not origin or not filetime or not sample_pid or
            not 0 < capacity <= CAPACITY or count > capacity or
            flags & ~(FLAG_FULL | FLAG_CLOCK_ERROR | FLAG_INCLUDE_BACKGROUND) or
            (flags & FLAG_FULL and count != capacity)):
        raise ValueError('Invalid host Present capture header')
    if len(capture) != HEADER.size + capacity * SAMPLE.size:
        raise ValueError('Host Present capture file is incomplete or the wrong size')
    if pid is not None and sample_pid != pid:
        raise ValueError('Host Present capture PID does not match the game')
    try:
        epoch = dt.datetime(1601, 1, 1, tzinfo=dt.timezone.utc)
        started = epoch + dt.timedelta(microseconds=filetime // 10)
    except OverflowError as error:
        raise ValueError('Invalid host Present capture UTC origin') from error

    warnings = []
    if flags & FLAG_FULL:
        warnings.append('capture capacity exhausted')
    if flags & FLAG_CLOCK_ERROR:
        warnings.append('performance counter failed')

    intervals = []
    first = last = None
    skipped_gaps = selected = 0
    previous_qpc, previous_attempt = origin, 0
    for index in range(count):
        qpc, attempt = SAMPLE.unpack_from(capture, HEADER.size + index * SAMPLE.size)
        if qpc < previous_qpc or attempt <= previous_attempt:
            raise ValueError(f'Host Present capture has a corrupt sample at index {index}')
        seconds = (qpc - origin) / frequency
        if start <= seconds and (end is None or seconds <= end):
            if selected:
                if attempt == previous_attempt + 1:
                    intervals.append((qpc - previous_qpc) * 1000 / frequency)
                else:
                    skipped_gaps += 1
            else:
                first = seconds
            last = seconds
            selected += 1
        previous_qpc, previous_attempt = qpc, attempt

    metrics = summarize(intervals)
    if metrics is None:
        reason = 'No usable host Present intervals in selected time range'
        if warnings:
            reason += ' (' + ', '.join(warnings) + ')'
        raise ValueError(reason)

    scope = BACKGROUND_SCOPE if flags & FLAG_INCLUDE_BACKGROUND else FOREGROUND_SCOPE
    return dict(source='host_dxgi_present', timing_scope=scope, status='partial' if warnings else 'completed',
                warnings=warnings, game_pid=sample_pid, capture_started_utc=started.isoformat(),
                frequency_hz=frequency, capacity=capacity, recorded_presents=count,
                start_s=start, end_s=end, first_s=first, last_s=last, selected_presents=selected,
                excluded_gap_intervals=skipped_gaps, metrics={'host_present_interval': metrics},
                mean_fps=1000 / metrics['mean_ms'])


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('capture')
    parser.add_argument('--start', type=float, default=0)
    parser.add_argument('--end', type=float)
    args = parser.parse_args()
    try:
        print(json.dumps(analyze_host_frames(args.capture, args.start, args.end), indent=2))
    except (OSError, ValueError) as error:
        parser.exit(1, f'{error}\n')
