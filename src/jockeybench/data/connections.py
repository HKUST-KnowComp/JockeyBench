"""Jockey and trainer form over the previous 365 days, from labels visible at a race's cutoff.

The index is built once in memory from finish labels; nothing is written to
the database and no 2026 row is read.
"""

from bisect import bisect_left
from collections import defaultdict
from functools import lru_cache

from ..clock import cutoff, release
from .projection import number

WINDOW_SECONDS = 365 * 86400


class ConnectionsIndex:
    def __init__(self, source):
        self.rows = defaultdict(list)
        # Select only facts needed for counting, not full pre/history blobs.
        query = '''SELECT r.day,r.off_at,r.pre_at,r.review_at,e.jockey_id,e.trainer_id,
                    json_extract(e.history,'$.finish') AS finish,
                    json_extract(e.history,'$.abnormal') AS abnormal
                   FROM entries e JOIN races r ON r.id=e.race_id
                   WHERE r.day<'2026-01-01' AND r.pre_at<=? ORDER BY r.off_at'''
        # The hard upper bound also excludes malformed future cards from the index.
        for row in source.con.execute(query, (cutoff('2026-01-01'),)):
            if row['pre_at'] > cutoff(row['day']):
                continue
            finish, abnormal = number(row['finish']), number(row['abnormal'])
            if finish is not None and finish <= 0:
                finish = None
            if abnormal in (1, 2) or (finish is None and abnormal in (None, 0)):
                continue  # scratched, or no usable result
            event = (
                row['off_at'],
                row['day'],
                row['review_at'],
                release(row['day']),
                int(finish == 1),
                int(finish is not None and 1 <= finish <= 3),
            )
            for kind in ('jockey', 'trainer'):
                if row[kind + '_id']:
                    self.rows[(kind, row[kind + '_id'])].append(event)
        self.times = {key: [r[0] for r in rows] for key, rows in self.rows.items()}
        self.counts = lru_cache(maxsize=8192)(self._counts)

    def _counts(self, kind, ident, asof, origin):
        """(starts, wins, top3) in the 365 days before ``asof`` whose label was public by ``asof``."""
        key = (kind, ident)
        events, times = self.rows.get(key, ()), self.times.get(key, ())
        lower, upper = bisect_left(times, asof - WINDOW_SECONDS), bisect_left(times, asof)
        starts = wins = top3 = 0
        for _, day, reviewed, released, win, top in events[lower:upper]:
            available = released if day >= origin else reviewed
            if available is None or available > asof:
                continue
            starts += 1
            wins += win
            top3 += top
        return starts, wins, top3


def connections(source, public, clock, origin):
    race = source.race(public, clock)
    asof = cutoff(race['day'])
    if not hasattr(source, '_connections_index'):
        source._connections_index = ConnectionsIndex(source)
    runners = []
    for row in source.con.execute('SELECT number,jockey_id,trainer_id FROM entries WHERE race_id=? ORDER BY number', (race['id'],)):
        values = {}
        for kind in ('jockey', 'trainer'):
            counts = source._connections_index.counts(kind, row[kind + '_id'], asof, origin)
            values.update({kind + '_' + field: count for field, count in zip(('starts', 'wins', 'top3'), counts, strict=True)})
        runners.append({'number': row['number'], 'values': values})
    return {'race_id': public, 'feature_asof': 'this_race_previous_day_21_jst', 'window_days': 365, 'runners': runners}
