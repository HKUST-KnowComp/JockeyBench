"""Original service metering around a trusted, non-exec policy interpreter."""
from jockeybench.research.runner import ActiveService
from jockeybench.errors import Rejected
from jockeybench.canonical import digest

class PolicyService(ActiveService):
    policy_job=None
    def dispatch(self,name,args,meter=None):
        if name=='compute' and self.policy_job is not None:
            source,callback=self.policy_job
            if args!={'code':source}:raise Rejected('Policy compute binding')
            package=callback()
            return {'result':{'EvidencePackage_digest':digest(package)},'stdout':'','sdk_misuse':[]}
        return super().dispatch(name,args,meter)
