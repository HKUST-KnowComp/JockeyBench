"""Read JRDB archives locally and decode their fixed-width Shift-JIS records.

A daily file ``CODEyymmdd.txt`` lives in its own ``.zip``/``.lzh`` or inside that
year's pack ``CODE_yyyy.lzh``. Offsets in the specifications are 1-based byte
positions; a field with OCC n repeats contiguously n times.
"""

import re
import subprocess
import tarfile
import tempfile
import warnings
import zipfile
from collections import OrderedDict
from collections.abc import Iterator
from pathlib import Path

ENCODING = 'cp932'


def archive_year(path: Path):
    """Year of an archive: ``CODE_yyyy.*`` (yearly pack) or ``CODEyymmdd.*`` (daily file); ``None`` for other names."""
    annual = re.fullmatch(r'[A-Za-z]+_(\d{4})', path.stem)
    if annual:
        return int(annual[1])
    daily = re.fullmatch(r'[A-Za-z]+(\d{6})', path.stem)
    if daily:
        yy = int(daily[1][:2])
        return (1900 if yy >= 90 else 2000) + yy
    return None


# Record type -> (archive directory, specification document). SRB records ship inside the SED archives.
TYPES = {
    'BAC': ('Bac', 'Bac/bac_doc.txt'),
    'KAB': ('Kab', 'Kab/kab_doc.txt'),
    'KYI': ('Kyi', 'Kyi/kyi_doc.txt'),
    'UKC': ('Ukc', 'Ukc/ukc_doc.txt'),
    'KKA': ('Kka', 'Kka/kka_doc.txt'),
    'CYB': ('Cyb', 'Cyb/cyb_doc.txt'),
    'CHA': ('Cha', 'Cha/cha_doc.txt'),
    'JOA': ('Jo', 'Jo/Jodata_doc2.txt'),
    'TYB': ('Tyb', 'Tyb/tyb_doc.txt'),
    'SED': ('Sed', 'Sed/sed_doc.txt'),
    'SKB': ('Skb', 'Skb/skb_doc.txt'),
    'SRB': ('Sed', 'Srb/srb_doc.txt'),
    'HJC': ('Hjc', 'Hjc/hjcdata_doc.txt'),
}


def list_members(archive: Path) -> list[str]:
    if archive.suffix.lower() == '.zip':
        return zipfile.ZipFile(archive).namelist()
    out = subprocess.run(['bsdtar', '-tf', str(archive)], capture_output=True, text=True, check=True).stdout
    return [line.strip() for line in out.splitlines() if line.strip()]


def read_member(archive: Path, member: str) -> bytes:
    if archive.suffix.lower() == '.zip':
        return zipfile.ZipFile(archive).read(member)
    try:
        return subprocess.run(['bsdtar', '-xOf', str(archive), member], capture_output=True, check=True).stdout
    except subprocess.CalledProcessError:
        # macOS libarchive rejects some valid LH5 streams (e.g. OZ221211).
        # lhafile independently enforces both the decoded size and the member CRC.
        import lhafile

        with archive.open('rb') as stream:
            return lhafile.Lhafile(stream).read(member)


class ArchiveReadCache:
    """Unpack each yearly LZH pack once into a temporary uncompressed tar; never extract paths."""

    def __init__(self, capacity=32):
        self.capacity, self.items, self.uncached = capacity, OrderedDict(), set()

    def read(self, archive, member):
        if archive.suffix.lower() == '.zip' or '_' not in archive.stem:
            return read_member(archive, member)
        key = str(archive.resolve())
        if key in self.uncached:
            return read_member(archive, member)
        if key not in self.items:
            temp = tempfile.TemporaryDirectory(prefix='jrdb-archive-')
            path = Path(temp.name) / 'cache.tar'
            try:
                subprocess.run(['bsdtar', '-cf', str(path), '@' + key], capture_output=True, check=True)
                handle = tarfile.open(path, 'r:')
            except subprocess.CalledProcessError:
                # A bad member elsewhere in a pack must not poison healthy days: read the requested
                # member with strict error checking instead; never use partial tar output.
                temp.cleanup()
                self.uncached.add(key)
                warnings.warn(f'Cannot cache {archive.name}; validating individual members instead.', RuntimeWarning, stacklevel=2)
                return read_member(archive, member)
            except BaseException:
                temp.cleanup()
                raise
            self.items[key] = (handle, temp)
            if len(self.items) > self.capacity:
                _, (old, folder) = self.items.popitem(last=False)
                old.close()
                folder.cleanup()
        self.items.move_to_end(key)
        stream = self.items[key][0].extractfile(member)
        if stream is None:
            raise ValueError('archive member is not a regular data file')
        with stream:
            return stream.read()

    def close(self):
        for handle, folder in self.items.values():
            handle.close()
            folder.cleanup()
        self.items.clear()
        self.uncached.clear()


def records(data: bytes, spec: dict) -> Iterator[dict]:
    """Decode fixed-width records. Field names keep the spec's Japanese; repeated fields get ``[k]``."""
    for line in data.replace(b'\r\n', b'\n').split(b'\n'):
        if not line.strip():
            continue
        record = {}
        for field in spec['fields']:
            occ = field['occ'] or 1
            for k in range(occ):
                start = field['offset'] - 1 + k * field['byte']
                end = start + field['byte']
                # A short older layout has unknown tail fields, never a partial observation.
                value = line[start:end].decode(ENCODING, errors='replace') if end <= len(line) else ''
                if '*' not in field['type'] or not value.strip():
                    value = value.strip()
                key = field['name'] if occ == 1 else f'{field["name"]}[{k + 1}]'
                # CHA contains two 追切種類 fields (runner and training partner); later homonyms keep their offset.
                if key in record:
                    key += f'@{field["offset"]}'
                record[key] = value
        yield record
