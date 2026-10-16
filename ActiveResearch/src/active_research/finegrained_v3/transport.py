"""Frozen transport with exact DeepSeek identity assertion; no parameter edits."""
from types import FunctionType,SimpleNamespace
from active_research.policy_amortized import transport as original
from active_research.aligned.integrity import IntegrityFailure
from active_research.llm.agent import redact
from pathlib import Path
MODEL='deepseek-ai/DeepSeek-V4-Flash-0731'
body=original.body

def checked_provider(module):
    def checked(service,settings,body,request_id,phase_key):
        if service.state.get('model_identity_hard_stop'):raise IntegrityFailure('V3 model identity previously failed')
        if body.get('model')!=MODEL:raise IntegrityFailure('V3 DeepSeek request identity')
        result,attempt=module.request(service,settings,body,request_id,phase_key)
        if result.get('model')!=MODEL:
            service.state['model_identity_hard_stop']={'expected':MODEL,'observed':redact(result.get('model')),'request_id':request_id,'phase':phase_key,
                'redacted_response':redact(result),'provider_attempt':redact(attempt)}
            service.state['pending']=None
            service.persist()
            from .policy_builder import write
            root=None
            if getattr(service,'path',None):root=Path(service.path).parent
            elif getattr(getattr(service,'config',None),'managed_root',None):root=Path(service.config.managed_root).parent
            if root is not None:write(root/'model-identity-hard-stop.json',service.state['model_identity_hard_stop'])
            raise IntegrityFailure('V3 DeepSeek response identity')
        return result,attempt
    return SimpleNamespace(request=checked)

request=FunctionType(original.request.__code__,{**original.request.__globals__,
    'provider':checked_provider(original.provider)},argdefs=original.request.__defaults__)
