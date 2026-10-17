"""Decode acquired archives into the private database the runtime reads.

Output tables: ``races`` and ``entries`` (one row per race and runner, with
whitelisted pre-race facts, result labels and settlement), ``expanded_race_ids``
(race id -> pseudonymous id, re-keyed by :mod:`.pseudonymize`), ``sources``
(provenance of every decoded archive member) and ``metadata``.
"""

import collections
import hashlib
import json
import re
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .archives import TYPES, ArchiveReadCache, archive_year, list_members, records
from .fields import COMMENTS, select
from .specs import parse_spec

JST = timezone(timedelta(hours=9))
IMPORT_VERSION = 'jockeybench-import-v1'
CODES = ['BAC', 'KAB', 'KYI', 'UKC', 'KKA', 'CYB', 'CHA', 'JOA', 'TYB', 'SED', 'SKB', 'SRB', 'HJC']
PRE_CODES = {'BAC', 'KAB', 'KYI', 'UKC', 'KKA', 'CYB', 'CHA', 'JOA'}
SCHEMA = """
CREATE TABLE metadata(key TEXT PRIMARY KEY,value TEXT NOT NULL);
CREATE TABLE sources(id TEXT PRIMARY KEY,code TEXT,day TEXT,archive TEXT,member TEXT,sha256 TEXT,captured_at INTEGER);
CREATE TABLE races(id TEXT PRIMARY KEY,day TEXT NOT NULL,off_at INTEGER NOT NULL,pre_at INTEGER NOT NULL,late_at INTEGER,pre_capture INTEGER,late_capture INTEGER,context TEXT NOT NULL,review TEXT NOT NULL,review_at INTEGER,review_capture INTEGER);
CREATE INDEX races_day ON races(day);
CREATE TABLE entries(race_id TEXT,number TEXT,horse_id TEXT,jockey_id TEXT,trainer_id TEXT,pre TEXT,late TEXT,history TEXT,comments TEXT,settlement TEXT,pre_sources TEXT,late_source TEXT,history_sources TEXT,review_at INTEGER,history_capture INTEGER,PRIMARY KEY(race_id,number));
CREATE INDEX horse_history ON entries(horse_id,review_at);
CREATE INDEX entry_review ON entries(review_at,race_id);
CREATE TABLE expanded_race_ids(race_id TEXT PRIMARY KEY,public_id TEXT UNIQUE);
"""


def stamp(day, hhmm='2100'):
    return int(datetime.strptime(day + hhmm, '%Y-%m-%d%H%M').replace(tzinfo=JST).timestamp())


def pre_release(day):
    """Card publication: 21:00 on the evening before race day."""
    return stamp(day) - 86400


def review_release(day):
    """Review publication: 18:00 on the following Thursday."""
    d = datetime.strptime(day, '%Y-%m-%d')
    return stamp((d + timedelta(days=(3 - d.weekday()) % 7 or 7)).strftime('%Y-%m-%d'), '1800')


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False, allow_nan=False).encode()).hexdigest()


def dump(value):
    return json.dumps(value, ensure_ascii=False, separators=(',', ':'), allow_nan=False)


def race_key(record):
    return ''.join(record.get(x, '') for x in ['場コード', '年', '回', '日', 'Ｒ'])


def legacy_token(kind, key):
    """Pre-key pseudonym; :mod:`.pseudonymize` re-keys it with the private namespace."""
    return kind + '_' + hashlib.sha256((kind + ':' + key).encode()).hexdigest()[:14]


class ArchiveCatalog:
    """Index archive members by (code, yymmdd); a daily file supersedes the same day in a yearly pack."""

    def __init__(self, root: Path, docs: Path, years):
        self.root, self.docs, self.specs, self.index, self.verified = root, docs, {}, {}, set()
        self.cache = ArchiveReadCache()
        self.manifest = {r['relpath']: r for r in map(json.loads, (root / 'manifest.jsonl').read_text().splitlines())}
        for directory in sorted({TYPES[c][0] for c in CODES}):
            paths = [p for p in (root / directory).glob('*') if p.suffix.lower() in {'.zip', '.lzh'} and archive_year(p) in years]
            for path in sorted(paths, key=lambda p: ('_' not in p.stem, p.suffix == '.zip', p.name)):
                for member in list_members(path):
                    match = re.fullmatch(r'([A-Z]+)(\d{6})\.txt', Path(member).name, re.I)
                    if match and match[1].upper() in CODES:
                        self.index[(match[1].upper(), match[2])] = (path, member)

    def days(self):
        return sorted({'20' + ymd[:2] + '-' + ymd[2:4] + '-' + ymd[4:] for code, ymd in self.index if code == 'BAC'})

    def read(self, code, day):
        """Decoded records and a provenance row for one code and day; ``([], None)`` when absent."""
        key = (code, day[2:].replace('-', ''))
        if key not in self.index:
            return [], None
        path, member = self.index[key]
        rel = str(path.relative_to(self.root))
        meta = self.manifest.get(rel)
        if meta is None:
            raise ValueError(f'unmanifested archive: {rel}')
        if rel not in self.verified:
            if hashlib.sha256(path.read_bytes()).hexdigest() != meta['sha256']:
                raise ValueError(f'archive hash mismatch: {rel}')
            self.verified.add(rel)
        raw = self.cache.read(path, member)
        member_hash = hashlib.sha256(raw).hexdigest()
        source = {
            'id': digest([rel, member, member_hash])[:24],
            'code': code,
            'day': day,
            'archive': rel,
            'member': member,
            'sha256': member_hash,
            'captured_at': int(datetime.fromisoformat(meta['fetched_at']).timestamp()),
        }
        if code == 'HJC':
            return [hjc_record(line) for line in raw.splitlines() if line.strip()], source
        if code not in self.specs:
            self.specs[code] = parse_spec(self.docs / TYPES[code][1])
        return list(records(raw, self.specs[code])), source

    def close(self):
        self.cache.close()


def hjc_record(line):
    """The race key and the WIN payouts (runner number -> yen per 100) of one HJC payout record."""
    if len(line) < 431:
        raise ValueError('truncated HJC record')
    wins = {}
    for j in range(3):
        part = line[8 + 9 * j : 17 + 9 * j]
        number, money = part[:2].decode('ascii').strip(), part[2:].decode('ascii').strip()
        if number and number != '00' and money and int(money) > 0:
            wins[number] = int(money)
    return {'race_key': line[:8].decode('ascii'), 'win_payouts': wins}


def build_database(raw, docs, database, start, end, progress=print):
    if start < '2015-01-01':
        raise ValueError('The importer supports 2015 onward; older layouts require a separate audit')
    catalog = ArchiveCatalog(Path(raw), Path(docs), list(range(int(start[:4]), int(end[:4]) + 1)))
    Path(database).parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(database)
    con.executescript(SCHEMA)
    con.execute('PRAGMA journal_mode=WAL')
    con.execute('PRAGMA synchronous=NORMAL')
    counts, missing = collections.Counter(), {}
    try:
        for day in catalog.days():
            if start <= day <= end:
                import_day(con, catalog, day, counts, missing)
                if counts['days_imported'] and counts['days_imported'] % 20 == 0:
                    progress(dump({'day': day, **counts}))
        provenance = [tuple(r) for r in con.execute('SELECT id,sha256 FROM sources ORDER BY id')]
        fingerprint = digest([provenance, IMPORT_VERSION])
        con.executemany('INSERT INTO metadata VALUES(?,?)', [('dataset_fingerprint', fingerprint), ('import_version', IMPORT_VERSION)])
        con.commit()
        con.execute('PRAGMA wal_checkpoint(TRUNCATE)')
    finally:
        con.close()
        catalog.close()
    report = {
        'counts': dict(counts),
        'missing_by_day': missing,
        'dataset_fingerprint': fingerprint,
        'temporal_fidelity': 'historical_reconstruction; original publication and revision timestamps generally unverified',
    }
    Path(str(database) + '.build.json').write_text(json.dumps(report, ensure_ascii=False, indent=2))
    return report


def import_day(con, catalog, day, counts, missing):
    data, sources = {}, {}
    for code in CODES:
        data[code], source = catalog.read(code, day)
        if source:
            sources[code] = source
    missing[day] = [c for c in CODES if not data[c]]
    if not data['KYI'] or not data['BAC']:
        counts['days_without_cards'] += 1
        return
    maps = {c: {(r.get('race_key') or race_key(r)) + r.get('馬番', ''): r for r in rows} for c, rows in data.items() if c != 'UKC'}
    horses = {r['血統登録番号']: r for r in data['UKC']}
    pre_codes = [c for c in sources if c in PRE_CODES]
    with con:
        for source in sources.values():
            con.execute('INSERT OR REPLACE INTO sources VALUES(:id,:code,:day,:archive,:member,:sha256,:captured_at)', source)
        for bac in data['BAC']:
            rid, off = race_key(bac), bac.get('発走時間', '')
            if len(rid) != 8 or not re.fullmatch(r'\d{4}', off):
                counts['invalid_races'] += 1
                continue
            try:
                off_at = stamp(day, off)
            except ValueError:
                counts['invalid_races'] += 1
                continue
            card = [r for r in data['KYI'] if race_key(r) == rid]
            if not card:
                continue
            context = select('BAC', bac)
            context['venue'] = int(rid[:2])
            # KAB has no race number: its six-byte meeting key is matched explicitly.
            context.update(select('KAB', next((r for r in data['KAB'] if race_key(r) == rid[:6]), {})))
            times = [maps['TYB'].get(rid + r['馬番'], {}).get('オッズ取得時間', '') for r in card]
            late_at = stamp(day, times[0]) if times and all(t == times[0] and re.fullmatch(r'\d{4}', t) for t in times) else None
            if late_at is not None and late_at >= off_at:
                late_at = None
            review = maps['SRB'].get(rid, {})
            con.execute(
                'INSERT OR REPLACE INTO races VALUES(?,?,?,?,?,?,?,?,?,?,?)',
                (
                    rid,
                    day,
                    off_at,
                    pre_release(day),
                    late_at,
                    max(sources[c]['captured_at'] for c in pre_codes),
                    sources.get('TYB', {}).get('captured_at'),
                    dump(context),
                    dump({k: review.get(k, '') for k in COMMENTS['SRB']}),
                    review_release(day),
                    sources.get('SRB', {}).get('captured_at'),
                ),
            )
            con.execute('INSERT OR REPLACE INTO expanded_race_ids VALUES(?,?)', (rid, legacy_token('race', rid)))
            hjc = maps['HJC'].get(rid, {}).get('win_payouts', {})
            for runner in card:
                insert_entry(con, day, rid, runner, maps, horses, hjc, sources, pre_codes)
                counts['entries'] += 1
            counts['races'] += 1
    counts['days_imported'] += 1


def insert_entry(con, day, rid, runner, maps, horses, hjc, sources, pre_codes):
    number, horse = runner['馬番'], runner['血統登録番号']
    key, uk = rid + number, horses.get(runner['血統登録番号'], {})
    pre = {c: select(c, runner if c == 'KYI' else maps[c].get(key, {})) for c in ['KYI', 'KKA', 'CYB', 'CHA', 'JOA']}
    pre['cancelled'] = runner.get('取消フラグ') == '1'
    pre['pedigree'] = {k: legacy_token('pedigree', uk[k]) for k in ['父馬名', '母父馬名'] if uk.get(k)}
    birthday = uk.get('生年月日', '')
    if len(birthday) == 8:
        try:
            pre['age_days'] = (datetime.fromisoformat(day) - datetime.strptime(birthday, '%Y%m%d')).days
        except ValueError:
            pass
    tyb = maps['TYB'].get(key, {})
    late = select('TYB', tyb)
    late['cancelled'] = tyb.get('取消フラグ') == '1'
    late['present'] = bool(tyb)
    sed = maps['SED'].get(key, {})
    history = select('SED', sed) if sed else {}
    history['venue'] = int(rid[:2])
    comments = {k: maps['SKB'].get(key, {}).get(k, '') for k in COMMENTS['SKB']}
    pre['training_comment'] = maps['CYB'].get(key, {}).get('調教コメント', '')
    settlement = {
        'present': bool(sed) and bool(hjc),
        'abnormal': sed.get('異常区分'),
        'finish': sed.get('着順'),
        'win_payout_yen': hjc.get(number, 0),
        'SED_win_payout_yen': sed.get('単勝', ''),
        'final_odds': sed.get('確定単勝オッズ', ''),
    }
    review_codes = [c for c in ['SED', 'SKB'] if c in sources]
    con.execute(
        'INSERT OR REPLACE INTO entries VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
        (
            rid,
            number,
            horse,
            runner.get('騎手コード', ''),
            runner.get('調教師コード', ''),
            dump(pre),
            dump(late),
            dump(history),
            dump(comments),
            dump(settlement),
            dump({c: sources[c]['id'] for c in pre_codes}),
            sources.get('TYB', {}).get('id'),
            dump({c: sources[c]['id'] for c in review_codes}),
            review_release(day),
            max((sources[c]['captured_at'] for c in review_codes), default=0),
        ),
    )
