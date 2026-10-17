"""Trusted, read-only adapter over the prepared archive.

All access is time-gated by a decision clock: a card is visible from its
cutoff, finish labels from day close (``release``) and review labels from their
publication time. Races on or after 2026-01-01 are never returned.
"""

import hashlib
import hmac
import json
import re
import sqlite3
import subprocess
from datetime import date, timedelta
from pathlib import Path

from ..canonical import digest, file_hash, packed
from ..clock import cutoff, release
from ..errors import Rejected
from .fields import CHA, CONTEXT, CYB, KKA, KYI, RACE, RESULT, RUNNER, TRAINING
from .projection import extend, labels, pedigree, projection

ANON_VERSION = 'jrdb-pseudonymization-v4'
FILTERS = {'day_min', 'day_max', 'distance_min', 'distance_max', 'surface', 'venue', 'condition'}
CONDITION_VALUES = {'A1', 'A2', 'A3', 'OP', '5', '10', '16'}
LABEL_POLICY = 'Visible objective labels only; attached form is frozen at each sample cutoff.'


def quiescent(path):
    """Refuse a database that has an active writer or an unmerged WAL."""
    path = Path(path).resolve(strict=True)
    wal = Path(str(path) + '-wal')
    if wal.exists() and wal.stat().st_size:
        raise Rejected('Source WAL is not empty; immutable access refused')
    try:
        proc = subprocess.run(['/usr/sbin/lsof', '-F', 'fa', '--', str(path)], capture_output=True, text=True, timeout=20)
    except (OSError, subprocess.TimeoutExpired) as e:
        raise Rejected('Cannot check active source writers') from e
    if proc.returncode not in (0, 1) or proc.stderr.strip():
        raise Rejected('Source writer check inconclusive')
    # lsof -F a reports r/w/u access, including SQLite write-capable connections.
    if re.search(r'^a[wu]$', proc.stdout, re.M):
        raise Rejected('A write-capable source handle is active')
    return path


def stat_signature(path):
    s = Path(path).stat()
    return [s.st_dev, s.st_ino, s.st_size, s.st_mtime_ns, s.st_ctime_ns]


def _day_index(day, origin):
    return (date.fromisoformat(day) - date.fromisoformat(origin)).days


class Source:
    def __init__(self, path, cache_mib=None):
        self.path = quiescent(path)
        self.signature = stat_signature(self.path)
        self.con = sqlite3.connect(self.path.as_uri() + '?mode=ro&immutable=1', uri=True)
        self.con.row_factory = sqlite3.Row
        self.con.execute('PRAGMA query_only=ON')
        if cache_mib is not None:
            self.con.execute(f'PRAGMA cache_size=-{int(cache_mib) * 1024}')
        self.con.execute('PRAGMA temp_store=MEMORY')
        self.con.execute('PRAGMA mmap_size=0')
        self._metadata()
        self.verify()

    def _metadata(self):
        meta = dict(
            self.con.execute(
                "SELECT key,value FROM metadata WHERE key IN "
                "('anonymization_receipt','anonymization_private_key','dataset_fingerprint','import_version')"
            )
        )
        receipt = json.loads(meta.get('anonymization_receipt', '{}'))
        if receipt.get('status') != 'ready' or receipt.get('version') != ANON_VERSION:
            raise Rejected('Source anonymization is not ready v4')
        self.key = bytes.fromhex(meta['anonymization_private_key'])
        if len(self.key) != 32:
            raise Rejected('Invalid source namespace')
        self.identity = {k: meta[k] for k in ('dataset_fingerprint', 'import_version')}
        self.identity.update(anonymization_version=ANON_VERSION, receipt_digest=digest(receipt))

    def verify(self):
        quiescent(self.path)
        if stat_signature(self.path) != self.signature:
            raise Rejected('Source changed during this run')

    def manifest(self):
        self.verify()
        result = dict(self.identity)
        result['sha256'] = file_hash(self.path)
        result['bytes'] = self.path.stat().st_size
        sidecar = Path(str(self.path) + '.anonymization.json')
        result['sidecar_sha256'] = file_hash(sidecar) if sidecar.exists() else None
        self.verify()
        return result

    def token(self, kind, raw):
        """Stable pseudonym of an archive identifier, keyed by the private namespace."""
        legacy = kind + '_' + hashlib.sha256((kind + ':' + str(raw)).encode()).hexdigest()[:14]
        return kind + '_' + hmac.new(self.key, packed([ANON_VERSION, 'entity', legacy]), hashlib.sha256).hexdigest()[:24]

    def raw_race(self, public):
        if not isinstance(public, str) or not re.fullmatch(r'race_[0-9a-f]{24}', public):
            raise Rejected('Unknown race')
        row = self.con.execute('SELECT race_id FROM expanded_race_ids WHERE public_id=?', (public,)).fetchone()
        if row is None:
            raise Rejected('Unknown race')
        return row[0]

    def race(self, public, clock):
        rid = self.raw_race(public)
        # No request may even return a 2026 race row.
        row = self.con.execute(
            "SELECT id,day,pre_at,review_at,context FROM races WHERE id=? AND day<'2026-01-01' AND pre_at<=?", (rid, clock)
        ).fetchone()
        if row is None or row['pre_at'] > cutoff(row['day']) or cutoff(row['day']) > clock:
            raise Rejected('Race is not available at this decision time')
        return row

    def card(self, public, clock, origin_day=None):
        """The previous-evening card. ``origin_day`` turns absolute days into day indices."""
        race = self.race(public, clock)
        rows = self.con.execute(
            'SELECT number,horse_id,jockey_id,trainer_id,pre FROM entries WHERE race_id=? ORDER BY number', (race['id'],)
        )
        runners = []
        for row in rows:
            pre = json.loads(row['pre'])
            facts, missing = projection(pre.get('KYI', {}), RUNNER)
            extend(facts, missing, pre, ('age_days',))
            training, training_missing = projection(pre.get('CHA', {}), TRAINING)
            extend(facts, missing, pre.get('KYI'), KYI)
            extend(facts, missing, pre.get('KKA'), KKA)
            pedigree(self, pre, facts, missing)
            extend(training, training_missing, pre.get('CHA'), CHA)
            extend(training, training_missing, pre.get('CYB'), CYB)
            ids = {kind + '_id': self.token(kind, row[kind + '_id']) for kind in ('horse', 'jockey', 'trainer') if row[kind + '_id']}
            runners.append(
                {
                    'number': row['number'],
                    **ids,
                    'facts': facts,
                    'training': training,
                    'missing': {'facts': missing, 'training': training_missing},
                    'announced_cancelled': pre.get('cancelled') if type(pre.get('cancelled')) is bool else None,
                }
            )
        context = json.loads(race['context'])
        facts, missing = projection(context, CONTEXT)
        extend(facts, missing, context, RACE)
        result = {'race_id': public, 'facts': facts, 'missing': missing, 'runners': runners}
        if origin_day is None:  # Callers without an origin (schedule, the batch tool, program context) read only runner numbers.
            result.update(day=race['day'], feature_cutoff=cutoff(race['day']))
        else:
            result.update(day_index=_day_index(race['day'], origin_day), feature_asof='previous_day_21_jst')
        return result

    def schedule(self, start, end, days, races_per_day, mode):
        """Pick the evaluated races: per day, races with a pre-race card and at least three runners."""
        selected = []
        for (d,) in self.con.execute(
            "SELECT DISTINCT day FROM races WHERE day>=? AND day<=? AND day<'2026-01-01' ORDER BY day", (start, end)
        ):
            last_off = self.con.execute('SELECT max(off_at) FROM races WHERE day=?', (d,)).fetchone()[0]
            if last_off >= release(d):
                raise Rejected('Scheduled race extends past the fixed day-close; timeline is invalid')
            races = []
            for row in self.con.execute('SELECT id,pre_at FROM races WHERE day=? ORDER BY off_at,id', (d,)):
                if row['pre_at'] <= cutoff(d):
                    public = self.token('race', row['id'])
                    if len(self.card(public, cutoff(d))['runners']) >= 3:
                        races.append(public)
                if mode == 'smoke' and len(races) >= races_per_day:
                    break
            if races:
                selected.append({'day': d, 'races': races})
            if len(selected) >= days:
                break
        if not selected:
            raise Rejected('No pre-race eligible batches in the specified window')
        return selected

    def settlements(self, public):
        """Trusted evaluator view of a race's outcome; never shown to agents."""
        rid = self.raw_race(public)
        result = {}
        for row in self.con.execute('SELECT number,settlement,history FROM entries WHERE race_id=?', (rid,)):
            value = json.loads(row['settlement'])
            history = json.loads(row['history'])
            # Result availability must not depend on payouts.
            value['result_present'] = history.get('finish') is not None and str(history.get('finish')).strip() != ''
            result[row['number']] = value
        return result

    def history_page(self, clock, origin, after, limit, anchor=None, filters=None, order='recent', scope='all', include_form=0):
        """One page of historical cards with the labels visible at ``clock``.

        ``scope='support'`` restricts to races before the window (``origin``)
        whose review was published. ``anchor=(race_id, number)`` restricts to
        earlier starts of that runner's horse. Returns ``(page, next_after)``.
        """
        filters = filters or {}
        if not isinstance(filters, dict) or set(filters) - FILTERS or order not in {'recent', 'oldest'} or scope not in {'all', 'support'}:
            raise Rejected('Invalid history filters, order or scope; see describe')
        args = [clock]
        where = "r.pre_at<=? AND r.day<'2026-01-01'"
        if scope == 'support':
            where += ' AND r.day<? AND r.review_at<=?'
            args.extend([origin, clock])
        for key, value in filters.items():
            if key in {'day_min', 'day_max'}:
                if type(value) is not int or not -50000 <= value <= 50000:
                    raise Rejected('Relative day filters must be integer day indices')
                where += ' AND r.day' + ('>=' if key == 'day_min' else '<=') + '?'
                args.append((date.fromisoformat(origin) + timedelta(days=value)).isoformat())
                continue
            column = 'distance' if key.startswith('distance_') else key
            if key == 'condition':
                if type(value) not in (int, str) or str(value) not in CONDITION_VALUES:
                    raise Rejected('Invalid reviewed condition filter')
                if str(value).isdigit():
                    value = int(value)
            elif type(value) is not int or not 0 <= value <= 10000:
                raise Rejected('Fact filters require bounded integer values')
            op = '>=' if key.endswith('_min') else '<=' if key.endswith('_max') else '='
            where += f" AND json_extract(r.context,'$.{column}') {op} ?"
            args.append(value)
        if after:
            where += " AND r.day||'/'||r.id" + ('<' if order == 'recent' else '>') + '?'
            args.append(after)
        if anchor:
            public, n = anchor
            race = self.race(public, clock)
            row = self.con.execute('SELECT horse_id FROM entries WHERE race_id=? AND number=?', (race['id'], n)).fetchone()
            if row is None:
                raise Rejected('Unknown runner')
            anchor_horse = self.token('horse', row['horse_id'])
            # An anchor horse's history never includes that race or later starts.
            where += ' AND EXISTS(SELECT 1 FROM entries e WHERE e.race_id=r.id AND e.horse_id=?) AND r.day<?'
            args.extend([row['horse_id'], race['day']])
        direction = 'DESC' if order == 'recent' else 'ASC'
        rows = self.con.execute(
            f'SELECT r.id,r.day FROM races r WHERE {where} ORDER BY r.day {direction},r.id {direction} LIMIT ?', (*args, limit + 1)
        ).fetchall()
        examples = []
        for row in rows[:limit]:
            public = self.token('race', row['id'])
            try:
                card = self.card(public, clock, origin)
            except Rejected:
                continue
            if anchor:
                card['runners'] = [r for r in card['runners'] if r.get('horse_id') == anchor_horse]
                card['partial_card'] = True
                if len(card['runners']) != 1:
                    raise Rejected('Anchor horse has ambiguous historical entry')
            example = {'card': card, 'labels': None, 'label_missing': None, 'label_scope': 'not_released'}
            outcome = self.objective_result(public, clock, origin)
            if outcome:
                selected = {r['number'] for r in card['runners']}
                records = [r for r in outcome['records'] if r['number'] in selected]
                example.update(
                    labels={r['number']: r['values'] for r in records},
                    label_missing={r['number']: r['missing'] for r in records},
                    label_scope=outcome['stage'],
                )
            if anchor:
                example['matched_number'] = card['runners'][0]['number']
            if include_form:
                # Attached features are always frozen at THAT race's decision time.
                example['form'] = self.form(public, clock, origin, include_form)
            examples.append(example)
        # The cursor payload stays with the trusted service; agents only see an HMAC token.
        next_after = rows[limit - 1]['day'] + '/' + rows[limit - 1]['id'] if len(rows) > limit else None
        return {'examples': examples, 'order': order, 'scope': scope, 'label_policy': LABEL_POLICY}, next_after

    def objective_result(self, public, clock, origin):
        """Finish labels after day close; review labels after their publication; else ``None``."""
        race = self.race(public, clock)
        online = race['day'] >= origin
        if clock < (release(race['day']) if online else (race['review_at'] or 10**20)):
            return None
        context = json.loads(race['context'])
        records = []
        for row in self.con.execute(
            'SELECT number,history,review_at,pre,jockey_id,trainer_id FROM entries WHERE race_id=? ORDER BY number', (race['id'],)
        ):
            raw = json.loads(row['history'])
            complete = row['review_at'] is not None and row['review_at'] <= clock
            values, missing = self._result_values(raw, complete)
            labels(self, raw, json.loads(row['pre']), context, row['jockey_id'], row['trainer_id'], complete, values, missing)
            records.append({'number': row['number'], 'values': values, 'missing': missing})
        stage = 'review' if all('not_released' not in r['missing'].values() for r in records) else 'finish_only'
        return {
            'race_id': public,
            'day_index': _day_index(race['day'], origin),
            'records': records,
            'release_policy': 'results-v1',
            'stage': stage,
        }

    def form(self, public, clock, origin, k):
        """Each runner's ``k`` most recent labelled starts, frozen at this race's cutoff."""
        if type(k) is not int or not 1 <= k <= 5:
            raise Rejected('form k must be an integer from 1 to 5')
        race = self.race(public, clock)
        asof = cutoff(race['day'])
        runners = []
        for horse in self.con.execute('SELECT number,horse_id FROM entries WHERE race_id=? ORDER BY number', (race['id'],)).fetchall():
            # Stream until k labelled starts; missing rows cannot hide older eligible facts.
            rows = self.con.execute(
                'SELECT r.id,r.day,r.review_at,r.context,e.history,e.pre,e.jockey_id,e.trainer_id,e.review_at AS entry_review '
                "FROM entries e JOIN races r ON r.id=e.race_id WHERE e.horse_id=? AND r.day<? AND r.day<'2026-01-01' "
                'AND r.pre_at<=? ORDER BY r.day DESC,r.id DESC',
                (horse['horse_id'], race['day'], asof),
            )
            starts = []
            for row in rows:
                if asof < (release(row['day']) if row['day'] >= origin else (row['review_at'] or 10**20)):
                    continue
                raw = json.loads(row['history'])
                complete = row['entry_review'] is not None and row['entry_review'] <= asof
                values, missing = self._result_values(raw, complete)
                # No result is not a labelled start; no payout dependency.
                if values.get('finish') is None and values.get('abnormal') is None:
                    continue
                labels(
                    self,
                    raw,
                    json.loads(row['pre']),
                    json.loads(row['context']),
                    row['jockey_id'],
                    row['trainer_id'],
                    complete,
                    values,
                    missing,
                )
                starts.append(
                    {
                        'race_id': self.token('race', row['id']),
                        'day_index': _day_index(row['day'], origin),
                        'values': values,
                        'missing': missing,
                    }
                )
                if len(starts) >= k:
                    break
            runners.append({'number': horse['number'], 'starts': starts})
        return {'race_id': public, 'feature_asof': 'this_race_previous_day_21_jst', 'k': k, 'runners': runners}

    @staticmethod
    def _result_values(raw, complete):
        """Result fields of one start: all of them after review, else finish/abnormal only."""
        values, missing = projection(raw, RESULT if complete else ('finish', 'abnormal'))
        for key in RESULT:
            if key not in values:
                values[key] = None
                missing[key] = 'not_released'
        return values, missing

    def close(self):
        self.con.close()
