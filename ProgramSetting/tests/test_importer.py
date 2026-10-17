"""The importer on fabricated fixed-width archives: manifest -> build -> anonymize -> read by the runtime."""

import json
import sqlite3
import zipfile
from pathlib import Path

import pytest

import jockeybench.data.source as source_module
from jockeybench.clock import cutoff
from jockeybench.data.source import Source
from jockeybench.importer.archives import TYPES, archive_year, records
from jockeybench.importer.build import ArchiveCatalog, build_database
from jockeybench.importer.cli import manifest, window
from jockeybench.importer.pseudonymize import anonymize, rekey
from jockeybench.importer.specs import parse_spec

KEY = [('場コード', 2), ('年', 2), ('回', 1), ('日', 1), ('Ｒ', 2)]
LAYOUTS = {
    'BAC': KEY + [('発走時間', 4), ('距離', 4), ('芝ダ障害コード', 1)],
    'KYI': KEY
    + [
        ('馬番', 2),
        ('血統登録番号', 8),
        ('騎手コード', 5),
        ('調教師コード', 5),
        ('枠番', 1),
        ('性別コード', 1),
        ('負担重量', 3),
        ('ＩＤＭ', 5),
        ('基準オッズ', 5),
        ('取消フラグ', 1),
    ],
    'UKC': [('血統登録番号', 8), ('父馬名', 12), ('母父馬名', 12), ('生年月日', 8)],
    'SED': KEY + [('馬番', 2), ('着順', 2), ('異常区分', 1), ('後３Ｆタイム', 3), ('確定単勝オッズ', 6)],
}
RACES = {'2023-12-02': ['05235101'], '2024-01-06': ['06241101', '06241102']}
HORSES = ['19100001', '19100002', '19100003']


def spec_document(fields):
    lines, offset = ['テスト仕様', '項目名  OCC  BYTE  TYPE  相対  備考'], 1
    for name, width in fields:
        lines.append(f'{name}  {width}  X  {offset}')
        offset += width
    return '\n'.join(lines + [f'レコード長：{offset + 1} BYTE']).encode('cp932')


def record(code, values):
    return b''.join(str(values.get(name, '')).encode('cp932').ljust(width)[:width] for name, width in LAYOUTS[code])


def race_fields(rid):
    return dict(zip([name for name, _ in KEY], [rid[:2], rid[2:4], rid[4], rid[5], rid[6:]], strict=True))


def write_archive(raw, code, day, lines):
    folder = raw / TYPES[code][0]
    folder.mkdir(parents=True, exist_ok=True)
    stem = code + day[2:].replace('-', '')
    with zipfile.ZipFile(folder / (stem + '.zip'), 'w') as archive:
        archive.writestr(stem + '.txt', b'\r\n'.join(lines) + b'\r\n')


@pytest.fixture
def archives(tmp_path):
    raw, docs = tmp_path / 'raw', tmp_path / 'docs'
    for code, fields in LAYOUTS.items():
        path = docs / TYPES[code][1]
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(spec_document(fields))
    for day, races in RACES.items():
        bac, kyi, sed, hjc = [], [], [], []
        for k, rid in enumerate(races):
            bac.append(record('BAC', {**race_fields(rid), '発走時間': f'{12 + k:02}00', '距離': 1600, '芝ダ障害コード': 1}))
            for n, horse in enumerate(HORSES, 1):
                number = f'{n:02}'
                kyi.append(
                    record(
                        'KYI',
                        {
                            **race_fields(rid),
                            '馬番': number,
                            '血統登録番号': horse,
                            '騎手コード': f'0100{n}',
                            '調教師コード': '01002',
                            '枠番': n,
                            '性別コード': 1,
                            '負担重量': 550,
                            'ＩＤＭ': 97531,
                            '基準オッズ': 86420,
                        },
                    )
                )
                sed.append(
                    record(
                        'SED',
                        {**race_fields(rid), '馬番': number, '着順': n, '異常区分': 0, '後３Ｆタイム': 350 + n, '確定単勝オッズ': 2.5 * n},
                    )
                )
            hjc.append((rid.encode() + b'01' + b'0000250').ljust(431))
        for code, lines in [
            ('BAC', bac),
            ('KYI', kyi),
            ('SED', sed),
            ('HJC', hjc),
            ('UKC', [record('UKC', {'血統登録番号': h, '父馬名': 'SIRE' + h[-1], '生年月日': '20190401'}) for h in HORSES]),
        ]:
            write_archive(raw, code, day, lines)
    return raw, docs


def test_spec_documents_decode_fixed_width_records(tmp_path):
    path = tmp_path / 'spec.txt'
    path.write_bytes(spec_document(LAYOUTS['SED']))
    spec = parse_spec(path)
    assert [f['name'] for f in spec['fields']] == [name for name, _ in LAYOUTS['SED']]
    line = record('SED', {**race_fields('06241101'), '馬番': '02', '着順': 2})
    assert next(records(line, spec))['着順'] == '2'
    assert list(records(line[:12], spec))[0]['後３Ｆタイム'] == ''  # short records never yield partial fields


def test_import_windows_exclude_the_2026_holdout(archives):
    raw, _ = archives
    with pytest.raises(ValueError, match='2026'):
        window('2025-01-01', '2026-01-01')
    write_archive(raw, 'BAC', '2026-01-04', [record('BAC', race_fields('06261101'))])
    result = manifest(raw, '2023-01-01', '2025-12-31', '2026-01-01T00:00:00+09:00')
    rows = [json.loads(line) for line in (raw / 'manifest.jsonl').read_text().splitlines()]
    assert result['archives'] == len(rows) == 10 and not [r for r in rows if '26' in Path(r['relpath']).stem[3:5]]
    with pytest.raises(ValueError, match='already exists'):
        manifest(raw, '2023-01-01', '2025-12-31', '2026-01-01T00:00:00+09:00')


def test_build_and_anonymize_produce_a_database_the_runtime_reads(archives, tmp_path, monkeypatch):
    raw, docs = archives
    manifest(raw, '2023-01-01', '2024-12-31', '2024-02-01T00:00:00+09:00')
    built = tmp_path / 'private' / 'built.sqlite'  # build creates the missing parent directory
    report = build_database(raw, docs, built, '2023-12-01', '2024-01-31', progress=lambda line: None)
    assert report['counts'] == {'days_imported': 2, 'races': 3, 'entries': 9}
    con = sqlite3.connect(built)
    legacy = dict(con.execute('SELECT race_id,public_id FROM expanded_race_ids'))
    con.close()

    key_path, prepared = tmp_path / 'private' / 'namespace.key', tmp_path / 'private' / 'expanded.sqlite'
    anonymize(built, prepared, key_path)
    assert oct(key_path.stat().st_mode)[-3:] == '600' and oct(prepared.stat().st_mode)[-3:] == '600'
    with pytest.raises(ValueError, match='exists'):
        anonymize(built, prepared, key_path)

    monkeypatch.setattr(source_module, 'quiescent', lambda path: Path(path).resolve())  # lsof is not what is tested here
    source = Source(prepared)
    try:
        assert source.identity['import_version'] == 'jockeybench-import-v1'
        assert source.token('race', '06241101') == rekey(key_path.read_bytes(), legacy['06241101'])
        schedule = source.schedule('2024-01-01', '2024-01-31', 10, 5, 'full')
        assert schedule == [{'day': '2024-01-06', 'races': [source.token('race', r) for r in RACES['2024-01-06']]}]
        card = source.card(schedule[0]['races'][0], cutoff('2024-01-06'), '2024-01-01')
        runner = card['runners'][0]
        assert card['facts']['distance'] == 1600 and card['facts']['venue'] == 6
        assert runner['facts']['draw'] == 1 and runner['facts']['age_days'] > 1000 and runner['facts']['sire_id'].startswith('pedigree_')
        text = json.dumps(card)
        assert not [s for s in ['19100001', '01001', 'SIRE', '97531', '86420', 'reference_win_odds', 'idm'] if s in text]
        page, _ = source.history_page(cutoff('2024-01-06'), '2024-01-01', '', 2, scope='support')
        assert len(page['examples']) == 1 and page['examples'][0]['labels']['01']['finish'] == 1
        settled = source.settlements(schedule[0]['races'][0])
        assert settled['01']['win_payout_yen'] == 250 and settled['01']['present'] and settled['02']['finish'] == '2'
    finally:
        source.close()


def test_manifest_and_build_select_archives_by_the_same_naming_rule():
    assert archive_year(Path('KAB_2025.lzh')) == 2025
    assert archive_year(Path('BAC240106.zip')) == 2024
    assert archive_year(Path('KYI991231.lzh')) == 1999
    for odd in ('SED_2024.old.lzh', 'KYI240106.txt.zip', 'x_BAC_2024.lzh'):
        assert archive_year(Path(odd)) is None


def test_manifest_and_build_select_the_same_archives(archives):
    raw, docs = archives
    with zipfile.ZipFile(raw / 'Bac' / 'BAC_2024.old.zip', 'w') as archive:  # off-pattern name, a day no other file has
        archive.writestr('BAC240108.txt', b'')
    manifest(raw, '2023-01-01', '2024-12-31', '2024-02-01T00:00:00+09:00')
    listed = {json.loads(line)['relpath'] for line in (raw / 'manifest.jsonl').read_text().splitlines()}
    catalog = ArchiveCatalog(raw, docs, [2023, 2024])
    assert {path.relative_to(raw).as_posix() for path, _ in catalog.index.values()} <= listed
    assert 'Bac/BAC_2024.old.zip' not in listed
