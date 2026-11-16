"""Validate a dump's PARAM.SFO before choosing guest addresses. No game code needed."""
import argparse
from pathlib import Path
import struct


def title_id(path):
    data = Path(path).read_bytes()
    if len(data) < 20 or data[:4] != b'\0PSF':
        raise ValueError('Invalid PARAM.SFO header')
    _, version, keys, values, count = struct.unpack_from('<5I', data)
    if version != 0x101 or not 20 + count * 16 <= keys <= values <= len(data):
        raise ValueError('Invalid PARAM.SFO table bounds')
    titles = []
    for index in range(count):
        key, fmt, size, capacity, offset = struct.unpack_from('<HHIII', data, 20 + index * 16)
        key_start = keys + key
        key_end = data.find(b'\0', key_start, values)
        if not keys <= key_start < values or key_end < 0 or size > capacity or values + offset + capacity > len(data):
            raise ValueError('Invalid PARAM.SFO entry bounds')
        if data[key_start:key_end] == b'TITLE_ID':
            value = data[values + offset:values + offset + size]
            if fmt != 0x204 or not value.endswith(b'\0'):
                raise ValueError('Invalid TITLE_ID string')
            titles.append(value[:-1].decode('ascii'))
    if len(titles) != 1 or titles[0] not in ('BCUS98119', 'BCES00220'):
        raise ValueError('Expected exactly one supported TITLE_ID: BCUS98119 or BCES00220')
    return titles[0]


def select_sfo(root, explicit=None):
    if explicit:
        path = Path(explicit)
        return title_id(path)
    paths = [Path(root) / name / 'PS3_GAME/PARAM.SFO' for name in
             ('Infamous (US)', 'Infamous (Europe) (Fr,De,Es,It,Nl)')]
    found = [path for path in paths if path.is_file()]
    if len(found) != 1:
        raise ValueError('Missing or ambiguous game dump; set INFAMOUS_GAME_SFO to the intended PARAM.SFO')
    return title_id(found[0])


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, required=True)
    parser.add_argument('--sfo', type=Path)
    args = parser.parse_args()
    try:
        print(select_sfo(args.root, args.sfo))
    except (OSError, ValueError) as error:
        parser.exit(1, f'Region validation failed: {error}\n')
