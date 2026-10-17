from jockeybench.research.policy_amortized.readable_evidence.report import provider_metrics, statistics


def test_failed_attempt_unknown_is_not_booked_as_reservation_cost():
    result=provider_metrics([{'run_id':'synthetic','schedule':[{'races':['race']}],
        'model_outputs':[{'prompt_tokens':100,'completion_tokens':50,'cost_usd':.01}],
        'llm_trajectory':[{'logical_request_elapsed_seconds':75}],
        'provider_attempts':[{'status':'http_error','attempt_index':0,'reserved_cost_usd':.2},
                             {'status':'complete','attempt_index':1}]}])
    assert result['api_estimate_usd']['sum']==.01
    assert result['logical_requests']==1 and result['provider_attempts']==2 and result['retry_count']==1
    assert len(result['failed_attempts_actual_charge_unknown'])==1
    assert result['aggregate_output_input_ratio']==.5
    assert result['latency_seconds']['mean']==75


def test_empty_provider_values_and_percentiles():
    assert statistics([])['max'] is None
    assert statistics([0,10])['p95']==9.5
