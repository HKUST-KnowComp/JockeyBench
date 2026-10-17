"""Parse JRDB's fixed-width specification documents (``<type>_doc.txt``, Shift-JIS) into record layouts.

Each specification lists fields as ``name  [OCC]  BYTE  TYPE  offset  [note]`` under a
``項目名 ... BYTE`` header and states ``レコード長：N BYTE``. ``parse_spec`` returns the
record length and the fields in document order, which is all a fixed-width reader needs.
"""

import re
from pathlib import Path

ROW = re.compile(
    r"^(?P<name>\S.*?)[ 　\t]+(?:(?P<occ>\d+)[ 　\t]+)?(?P<byte>\d+)[ 　\t]+(?P<type>[0-9ZXF.*\-]+)[ 　\t]+(?P<off>\d+)(?:[ 　\t]+(?P<note>.*))?$"
)


def decode(raw: bytes) -> str:
    try:
        return raw.decode('cp932')
    except UnicodeDecodeError:
        try:
            return raw.decode('utf-8')
        except UnicodeDecodeError:
            return raw.decode('cp932', errors='replace')


def parse_spec(path: Path) -> dict:
    text = decode(Path(path).read_bytes())
    length = re.search(r'レコード長[：:]\s*(\d+)', text)
    lines = text.splitlines()
    start = next((i for i, line in enumerate(lines) if '項目名' in line and ('BYTE' in line or '相対' in line)), None)
    fields = []
    for line in lines[start + 1 :] if start is not None else []:
        raw = line.rstrip()
        if not raw.strip() or (raw.strip().startswith('*') and len(raw.strip()) > 20):
            continue
        match = ROW.match(raw.strip())
        if match:
            fields.append(
                {
                    'name': match['name'].strip(),
                    'occ': int(match['occ']) if match['occ'] else None,
                    'byte': int(match['byte']),
                    'type': match['type'],
                    'offset': int(match['off']),
                }
            )
    return {'file': str(path), 'record_length': int(length[1]) if length else None, 'fields': fields}
