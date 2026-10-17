"""Evidence cells: auditable access units, not Shannon information or predictive value.

One cell is one scalar fact slot delivered, nulls included. A read is a
*repeat* when the same tool and canonical arguments return an identical
response digest within one trajectory; repeats carry no evidence fee.
"""

from ..canonical import digest
from ..errors import Rejected

PROGRAM_BUDGET_VERSION = 'spec-reproducibility-v060'
REPEAT_RULE = (
    'Within one agent’s own trajectory (its initial exploration is shared by that agent’s own arms only), the same tool and canonical arguments '
    '(excluding max_spend) with an identical response digest is a repeat: evidence fee and charged_cells '
    'are zero and no session or per-race evidence allowance is consumed. Call fees and call/byte counters '
    'are unchanged. A changed response digest is charged in full. Only digests are retained, never data pages.'
)
ENVELOPE_KEYS = {'_cost', '_ledger', '_usage', '_budget'}


def read_identity(tool, arguments, response):
    """(query key, data digest) of a data read, ignoring spend limits and accounting envelopes."""
    return (
        digest([tool, {k: v for k, v in arguments.items() if k != 'max_spend'}]),
        digest({k: v for k, v in response.items() if k not in ENVELOPE_KEYS}),
    )


def repeat_index(audit):
    """Rebuild delivered-read identities and check evidence receipts without payloads."""
    index = {}
    for row in audit:
        if row.get('status') != 'ok' or 'read_key' not in row:
            continue
        seen = index.setdefault(row['read_key'], [])
        repeated = row['data_digest'] in seen
        charged = 0 if repeated else row['evidence_cells']
        receipt = row['_cost']
        if receipt.get('repeat') != repeated or receipt.get('charged_cells') != charged or row.get('charged_cells') != charged:
            raise Rejected('Repeat-read receipt mismatch')
        if not repeated:
            seen.append(row['data_digest'])
    return index


def card_cells(card):
    # Race id/day plus objective values. Each runner has number, entity IDs,
    # announced status, facts and workout values. Nulls still occupy a slot.
    return (
        2
        + len(card['facts'])
        + sum(
            2 + sum(k in r for k in ('horse_id', 'jockey_id', 'trainer_id')) + len(r['facts']) + len(r['training']) for r in card['runners']
        )
    )


def example_cells(example):
    return card_cells(example['card']) + sum(len(label) for label in (example['labels'] or {}).values())


def coverage_cells(examples):
    return sum(len(e['card']['facts']) + sum(len(r['facts']) + len(r['training']) for r in e['card']['runners']) for e in examples)


def statistics_cells(examples, group):
    count = 0
    for example in examples:
        if example['labels'] is None:
            continue  # Unreleased labels never contribute to the statistic.
        n = len(example['card']['runners'])
        count += n + (n if group in {'draw', 'sex'} else 1)
    return count


def form_cells(value):
    return 1 + sum(1 + sum(2 + len(s['values']) for s in r['starts']) for r in value['runners'])


def result_cells(value):
    return 2 + sum(1 + len(r['values']) for r in value['records'])


def connection_cells(value):
    return 1 + sum(1 + len(r['values']) for r in value['runners'])


def training_cells(card):
    return 1 + sum(1 + len(r['training']) for r in card['runners'])
