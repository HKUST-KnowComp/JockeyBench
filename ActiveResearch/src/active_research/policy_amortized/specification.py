from active_research.aligned.specification import PROFILE as ALIGNED
from jrdb_lh.common import digest, Rejected

ARMS = ('adaptive_research_program', 'frozen_research_program')
FRAME = 'research-program-frame-v1'
PROFILE = {
    'experiment_protocol': 'Policy-Amortized-Research-v1.1',
    'usage_accounting_protocol': 'policy-session-usage-v1',
    'research_program_contract': 'research-program-v1',
    'experience_frame_protocol': FRAME,
    'policy_update_protocol': 'daily-policy-revision-v1',
    'initial_policy_matched': True,
    'per_race_prediction_llm': True, 'per_race_planning_llm': False,
    'research_program_llm_calls': 0, 'normal_prediction_requests_per_race': 1,
    'prediction_execution_profile': 'A2-Aligned-v1',
    'aligned_execution_hash': digest(ALIGNED),
    'failure_continuation_policy': 'graceful-delivery-v1',
    'scoring_profile': 'common-aligned-delivery-v1',
    'context_limit_bytes': 262144,
    'provider_budget_policy': 'per_race',
    'provider_estimated_safety_ceiling_usd': .75,
    'builder_max_turns': 40, 'updater_max_turns': 40,
    'builder_provider_safety_ceiling_usd': .75,
    'updater_provider_safety_ceiling_usd': .75,
    'builder_validation': 'synthetic-only-v1',
    'policy_interpreter': 'restricted-python-ast-v1',
    'automatic_restart': False, 'resume_existing_run': False,
    'context_compression': False, 'query_budget': None,
}

def validate_manifest(m):
    if any(m.get(k) != v for k,v in PROFILE.items()) or m.get('scientific_arm') not in ARMS:
        raise Rejected('Policy-Amortized frozen profile mismatch')
    if m.get('profile_hash') != digest(PROFILE): raise Rejected('Profile hash mismatch')
