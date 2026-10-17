"""Typed projection of raw archive records onto whitelisted fields.

Every projected field is present: a value, or ``None`` with a missing reason
(``absent``, ``blank``, ``invalid``, ``unusable`` or ``not_released``). Values
are never imputed and free text never passes through.
"""

import re

from .fields import LABELS, PAST_CARD, POSITIVE, REVIEWED_CONDITIONS, RUN_DETAILS

_FULL_WIDTH_SIGNS = str.maketrans({'＋': '+', '－': '-'})


def number(value):
    """Parse a numeric archive value; ``None`` for booleans, text and absurd magnitudes."""
    if isinstance(value, bool):
        return None
    if isinstance(value, str):
        value = re.sub(r'^([+-])\s+(?=\d)', r'\1', value.strip().translate(_FULL_WIDTH_SIGNS))
    try:
        n = float(value)
    except (ValueError, TypeError, OverflowError):
        return None
    if not -1e12 < n < 1e12:
        return None
    return int(n) if n.is_integer() else n


def projection(raw, fields):
    """Return ``(values, missing)`` for ``fields`` of the raw record ``raw``."""
    raw = raw if isinstance(raw, dict) else {}
    values, missing = {}, {}
    for key in fields:
        value = raw.get(key)
        blank = value is None or isinstance(value, str) and not value.strip()
        reason = 'absent' if key not in raw else 'blank' if blank else 'invalid'
        parsed = number(value)
        if key == 'condition' and isinstance(value, str) and value.strip() in REVIEWED_CONDITIONS:
            parsed = value.strip()
        if key in POSITIVE and isinstance(parsed, (int, float)) and parsed <= 0:
            parsed, reason = None, 'unusable'
        values[key] = parsed
        if parsed is None:
            missing[key] = reason
    return values, missing


def extend(values, missing, raw, fields):
    extra, absent = projection(raw, fields)
    values.update(extra)
    missing.update(absent)


def pedigree(source, pre, values, missing):
    """Sire and damsire as pseudonymous tokens; names never leave the trusted side."""
    raw = pre.get('pedigree')
    raw = raw if isinstance(raw, dict) else {}
    for field, key in [('sire_id', '父馬名'), ('damsire_id', '母父馬名')]:
        name = raw.get(key)
        values[field] = source.token('pedigree', name.strip()) if isinstance(name, str) and name.strip() else None
        if values[field] is None:
            missing[field] = 'blank' if key in raw else 'absent'


def labels(source, raw, pre, context, jockey, trainer, complete, values, missing):
    """Review-stage label fields of a past start; withheld until the review is released."""
    if not complete:
        values.update(dict.fromkeys(LABELS))
        missing.update(dict.fromkeys(LABELS, 'not_released'))
        return
    extend(values, missing, raw, RUN_DETAILS)
    values['slow_start'] = raw.get('start_adjustment') is not None
    extend(values, missing, context, PAST_CARD[:4])
    extend(values, missing, pre.get('KYI'), ('draw',))
    for kind, ident in [('jockey', jockey), ('trainer', trainer)]:
        key = kind + '_id'
        values[key] = source.token(kind, ident) if ident else None
        if values[key] is None:
            missing[key] = 'absent'
