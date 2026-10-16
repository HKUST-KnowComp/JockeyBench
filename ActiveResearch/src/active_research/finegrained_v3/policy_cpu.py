"""Generated computation CPU only; trusted SDK work has an excluded interval."""
import time
class PolicyCPU:
    def __init__(self,limit):
        self.limit=limit;self.started=time.process_time();self.excluded=0.;self.depth=0;self.finished=None
    def trusted(self,callback):
        if self.depth:return callback()
        before=time.process_time();self.depth+=1
        try:return callback()
        finally:self.depth-=1;self.excluded+=time.process_time()-before
    def snapshot(self,steps):
        total=max(0.,time.process_time()-self.started)
        return {'version':'generated-policy-cpu-v1','limit_seconds':self.limit,'policy_cpu_seconds':max(0.,total-self.excluded),'trusted_cpu_seconds':self.excluded,'process_cpu_seconds':total,'interpreter_steps':steps}
    def exceeds(self):return self.limit is not None and time.process_time()-self.started-self.excluded>self.limit
