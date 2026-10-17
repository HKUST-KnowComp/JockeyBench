"""Portable system-tool handling, using fabricated files and archives only."""

import struct
import subprocess
from types import SimpleNamespace

import lhafile
import pytest

import jockeybench.data.source as source_module
from jockeybench.errors import Rejected
from jockeybench.importer import archives


@pytest.fixture
def source_file(tmp_path):
    path = tmp_path / 'synthetic-source'
    path.write_bytes(b'writer-check fixture; not a database')
    return path


@pytest.fixture
def lsof_path(tmp_path, monkeypatch):
    directory = tmp_path / 'bin'
    directory.mkdir()
    executable = directory / 'lsof'
    executable.write_text('#!/bin/sh\nexit 1\n')
    executable.chmod(0o755)
    monkeypatch.setenv('PATH', str(directory))
    return executable


def test_source_writer_check_resolves_lsof_from_path(source_file, lsof_path, monkeypatch):
    calls = []

    def run(command, **kwargs):
        calls.append((command, kwargs))
        return SimpleNamespace(returncode=0, stdout='p123\nf4\nar\n', stderr='')

    monkeypatch.setattr(source_module.subprocess, 'run', run)
    assert source_module.quiescent(source_file) == source_file.resolve()
    assert calls == [
        ([str(lsof_path), '-F', 'fa', '--', str(source_file.resolve())], {'capture_output': True, 'text': True, 'timeout': 20})
    ]


def test_source_writer_check_refuses_missing_lsof(source_file, tmp_path, monkeypatch):
    monkeypatch.setenv('PATH', str(tmp_path))
    monkeypatch.setattr(source_module.subprocess, 'run', lambda *a, **k: pytest.fail('Missing lsof must not launch another command'))
    with pytest.raises(Rejected, match='Cannot check active source writers'):
        source_module.quiescent(source_file)


@pytest.mark.parametrize(
    ('returncode', 'stdout', 'stderr', 'error'),
    [
        (0, 'p123\nf4\naw\n', '', 'A write-capable source handle is active'),
        (0, 'p123\nf4\nau\n', '', 'A write-capable source handle is active'),
        (0, '', 'some processes could not be checked', 'Source writer check inconclusive'),
        (2, '', '', 'Source writer check inconclusive'),
    ],
)
def test_source_writer_check_still_fails_closed(source_file, lsof_path, monkeypatch, returncode, stdout, stderr, error):
    monkeypatch.setattr(
        source_module.subprocess, 'run', lambda *a, **k: SimpleNamespace(returncode=returncode, stdout=stdout, stderr=stderr)
    )
    with pytest.raises(Rejected, match=error):
        source_module.quiescent(source_file)


@pytest.mark.parametrize('failure', [FileNotFoundError('lsof disappeared'), subprocess.TimeoutExpired('lsof', 20)])
def test_source_writer_check_refuses_failed_probe(source_file, lsof_path, monkeypatch, failure):
    def run(*args, **kwargs):
        raise failure

    monkeypatch.setattr(source_module.subprocess, 'run', run)
    with pytest.raises(Rejected, match='Cannot check active source writers'):
        source_module.quiescent(source_file)


def synthetic_lha(members):
    """Generate level-0 stored LHA entries with an independently computed CRC-16."""
    result = bytearray()
    for name, data in members.items():
        name = name.encode('ascii')
        crc = 0
        for byte in data:
            crc ^= byte
            for _ in range(8):
                crc = (crc >> 1) ^ (0xA001 if crc & 1 else 0)
        header = struct.pack('<5sIIIBBB', b'-lh0-', len(data), len(data), 0, 0x20, 0, len(name)) + name + struct.pack('<H', crc)
        result.extend(bytes((len(header), sum(header) & 0xFF)) + header + data)
    return bytes(result) + b'\0'


@pytest.fixture
def lha_archive(tmp_path):
    path = tmp_path / 'BAC_2024.lzh'
    members = {'BAC240106.txt': b'fabricated first day\r\n', 'BAC240107.txt': b'fabricated second day\r\n'}
    path.write_bytes(synthetic_lha(members))
    return path, members


@pytest.fixture(params=['missing', 'failed'])
def unavailable_bsdtar(request, monkeypatch):
    def run(command, **kwargs):
        assert command[0] == 'bsdtar'
        if request.param == 'missing':
            raise FileNotFoundError('bsdtar')
        raise subprocess.CalledProcessError(1, command)

    monkeypatch.setattr(archives.subprocess, 'run', run)


def test_lha_listing_and_reading_without_working_bsdtar(lha_archive, unavailable_bsdtar):
    path, members = lha_archive
    assert archives.list_members(path) == list(members)
    for name, data in members.items():
        assert archives.read_member(path, name) == data


def test_lha_cache_falls_back_to_checked_individual_members(lha_archive, unavailable_bsdtar):
    path, members = lha_archive
    cache = archives.ArchiveReadCache()
    try:
        with pytest.warns(RuntimeWarning, match='validating individual members'):
            assert cache.read(path, 'BAC240106.txt') == members['BAC240106.txt']
        assert not cache.items
        assert cache.read(path, 'BAC240107.txt') == members['BAC240107.txt']
    finally:
        cache.close()


def test_lha_fallback_rejects_corrupt_member(lha_archive, unavailable_bsdtar):
    path, _ = lha_archive
    data = bytearray(path.read_bytes())
    data[-2] ^= 1
    path.write_bytes(data)
    with pytest.raises(lhafile.BadLhafile, match='crc'):
        archives.read_member(path, 'BAC240107.txt')
