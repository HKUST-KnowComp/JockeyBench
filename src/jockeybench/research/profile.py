"""Public research profile, separate from the frozen historical deployment."""

import math
from urllib.parse import urlsplit

from jockeybench.agents import provider
from jockeybench.errors import Rejected

VERSION = 'jockeybench-research-v1'
ARMS = {'adaptive': 'adaptive_research_program', 'frozen': 'frozen_research_program'}


def validate_config(config):
    from .common_scoring import compatible

    compatible(config.tariff)
    if config.arm != 'A' or config.tariff.hit_bonus_yen != 0:
        raise Rejected('Research uses core arm A and hit_bonus_yen=0; choose adaptive/frozen with the research --arm option')
    return config


def provider_settings(raw):
    """Validate user-supplied prices and limits without credentials or network I/O."""
    allowed = {
        'research_profile',
        'model',
        'base_url',
        'api_key_env',
        'timeout_seconds',
        'temperature',
        'seed',
        'reasoning_effort',
        'output_token_field',
        'input_usd_per_million',
        'output_usd_per_million',
        'max_estimated_cost_usd',
        'retry_attempts',
        'retry_base_seconds',
        'retry_max_seconds',
        'reservation_output_tokens',
    }
    if not isinstance(raw, dict) or set(raw) - allowed:
        raise Rejected('Research provider settings contain unknown fields; configure secrets through an environment variable')
    settings = {**raw, 'research_profile': VERSION}
    if raw.get('research_profile', VERSION) != VERSION:
        raise Rejected('Unknown public research profile')
    for key in ('model', 'api_key_env'):
        if not isinstance(settings.get(key), str) or not settings[key].strip():
            raise Rejected(f'Research provider requires {key}')
    if not isinstance(settings.get('base_url'), str):
        raise Rejected('Research provider requires a base_url string')
    url = urlsplit(settings['base_url'])
    if url.scheme not in {'https', 'http'} or not url.hostname or url.username or url.password or url.query or url.fragment:
        raise Rejected('Use an HTTP(S) chat-completions base URL without credentials or query parameters')
    if settings.get('output_token_field') != 'max_tokens':
        raise Rejected('Research native tools currently require output_token_field=max_tokens')
    for key in ('input_usd_per_million', 'output_usd_per_million', 'max_estimated_cost_usd', 'timeout_seconds'):
        value = settings.get(key)
        if type(value) not in (int, float) or not math.isfinite(value) or value <= 0:
            raise Rejected(f'Research provider requires a positive finite {key}')
    for key in ('reservation_output_tokens', 'retry_attempts'):
        if type(settings.get(key)) is not int or settings[key] < 1:
            raise Rejected(f'Research provider requires a positive integer {key}')
    for key in ('retry_base_seconds', 'retry_max_seconds'):
        value = settings.get(key, provider.RETRY_DEFAULTS[key])
        if type(value) not in (int, float) or not math.isfinite(value) or value <= 0:
            raise Rejected(f'Invalid {key}')
    provider.require_prices(settings)
    return settings
