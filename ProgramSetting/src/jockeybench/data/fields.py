"""The reviewed whitelist of archive fields an agent may see (projection ``objective-v5``).

Only objective, pre-race facts and released results are exposed. Odds,
popularity, payouts, JRDB indices and ratings, predictions and free text are
never projected; ``FORBIDDEN`` records the families that were reviewed and
excluded.
"""

# Race card facts (BAC/KAB).
CONTEXT = (
    'distance',
    'surface',
    'direction',
    'course_layout',
    'age_class',
    'condition',
    'weight_rule',
    'grade',
    'declared_runners',
    'course_variant',
    'venue',
    'meeting_day',
    'grass_type',
    'grass_height',
    'rolling',
    'antifreeze',
    'rainfall',
)
RACE = ('forecast_weather', 'forecast_turf_going', 'forecast_dirt_going', 'turf_inner', 'turf_middle', 'turf_outer')

# Runner card facts (KYI, KKA and pedigree).
RUNNER = (
    'carried_weight_tenths_kg',
    'draw',
    'sex',
    'blinkers',
    'layoff_weeks',
    'card_body_weight',
    'card_body_weight_change',
    'transport',
    'earned_prize',
    'class_prize',
    'starts_since_stabling',
    'days_since_stabling',
)
KYI = ('class_drop', 'rest_reason', 'hoof_code', 'late_start_rate')
KKA = ('sire_turf_top2', 'sire_dirt_top2', 'sire_average_distance', 'damsire_turf_top2', 'damsire_dirt_top2', 'damsire_average_distance')
PEDIGREE = ('sire_id', 'damsire_id')

# Workout facts (CHA, CYB).
TRAINING = ('furlongs', 'early_time', 'middle_time', 'late_time')
CHA = ('effort', 'urging', 'rider_role', 'partner_result', 'partner_class', 'partner_age', 'partner_effort')
CYB = ('course_type', 'distance_type')

# Result labels of a past start (SED). ``RESULT`` fields arrive at day close or
# review publication; the remaining label fields arrive with the review.
RESULT = (
    'finish',
    'abnormal',
    'distance',
    'surface',
    'carried_weight_tenths_kg',
    'early3f_tenths_sec',
    'last3f_tenths_sec',
    'margin_tenths_sec',
    'corner4_rank',
    'body_weight',
    'body_weight_change',
    'going',
    'venue',
)
RUN_DETAILS = (
    'corner4_path',
    'path',
    'trouble',
    'early_trouble',
    'middle_trouble',
    'late_trouble',
    'race_pace',
    'horse_pace',
    'running_style',
)
PAST_CARD = ('condition', 'grade', 'age_class', 'declared_runners', 'draw', 'jockey_id', 'trainer_id')
LABELS = RUN_DETAILS + ('slow_start',) + PAST_CARD

CONNECTIONS = ('jockey_starts', 'jockey_wins', 'jockey_top3', 'trainer_starts', 'trainer_wins', 'trainer_top3')

# Strictly positive quantities: zero or negative values are recorded as unusable.
POSITIVE = {'card_body_weight', 'body_weight', 'distance', 'age_days', 'early_time', 'middle_time', 'late_time', 'furlongs', 'finish'}
REVIEWED_CONDITIONS = {'A1', 'A2', 'A3', 'OP'}

# Regression inventory; none of these families are projection sources.
FORBIDDEN = (
    'KYI.*index',
    'idm',
    '*_aptitude',
    'improvement',
    'ability_class',
    'stable_rank',
    'stable_rating',
    'external_stable_rank',
    'card.running_style',
    'training_trend',
    'consensus_votes_*',
    'specialist_votes_*',
    'reference_*',
    'popularity_index',
    'predicted_*',
    'jockey_expected_*',
    'upset_index',
    'start_index',
    'JOA.*',
    'horse_note*',
    'training_comment',
    'entries.comments',
    'races.review',
    'settlement.*',
    'forecast_turf_bias',
    'forecast_dirt_bias',
    'CYB.*index',
    'CYB.*rating',
    'CYB.type',
    'CYB.emphasis',
    'CYB.readiness_*',
    'observed_idm',
    'raw_ability',
    '*_index',
    '*_adjustment',
    'historical_final_odds',
)
