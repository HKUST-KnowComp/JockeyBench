"""Scientific arms share this single immutable execution profile."""
from jockeybench.canonical import digest
from jockeybench.errors import Rejected

PROTOCOL = 'A2-Aligned-v1'
ARMS = ('guided_experience', 'frozen_neutral_history')
PROFILE = {
    'experiment_protocol': PROTOCOL,
    'research_semantics_base': 'A2-experience-guided-v1',
    'execution_profile': 'modeling-legacy-transport-aligned-v1',
    'strict_paradigm_match': False,
    'provider_timeout_seconds': 3600,
    'provider_max_attempts': 10,
    'retry_backoff_base_seconds': 2,
    'retry_backoff_max_seconds': 300,
    'max_output_tokens': None,
    'max_model_turns_per_race': 40,
    'request_body_limit_bytes': 262144,
    'race_wall_clock_deadline': None,
    'provider_budget_policy': 'per_race',
    'provider_estimated_safety_ceiling_usd': .75,
    'trajectory_provider_budget_match': False,
    'context_retention_policy': 'functionally different',
    'automatic_fallback': True,
    'automatic_restart': False,
    'resume_existing_race': False,
    'extra_recovery_cycles': 0,
    'failure_continuation_policy':'graceful-delivery-v1',
    'finalization_repair':{'enabled':True,'max_logical_requests_per_race':1,
                           'research_tools_enabled':False,'allowed_action':'submit'},
    'execution_fallback':{'enabled':True,'type':'deterministic_uniform','top1_bonus_eligible':False},
    'delivery_scoring_profile':'common-aligned-delivery-v1',
    'scoring_profile':'common-aligned-delivery-v1',
    'direct_predictor_bonus_eligible':True,
    'repaired_predictor_bonus_eligible':True,
    'uniform_submit_bonus_eligible':False,
    'execution_fallback_bonus_eligible':False,
}
UNMATCHED = ('information_interface', 'phase_budget_allocation',
             'context_retention_policy', 'failure_continuation_policy',
             'trajectory_provider_budget', 'historical_provider_time_variation')

def execution_hash():
    return digest(PROFILE)

def validate_manifest(manifest):
    if manifest.get('scientific_arm') not in ARMS:
        raise Rejected('Unknown aligned scientific arm')
    if any(manifest.get(k) != v for k, v in PROFILE.items()):
        raise Rejected('Aligned execution profile mismatch')
    if manifest.get('execution_profile_hash') != execution_hash():
        raise Rejected('Aligned execution profile hash mismatch')
