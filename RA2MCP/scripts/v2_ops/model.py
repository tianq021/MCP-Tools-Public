"""One checkpoint and forward pass: frozen v2 forecast encoder + learned control."""
from pathlib import Path
from collections import deque
import copy,sys
import numpy as np
import torch
from torch import nn
ROOT=Path('E:/RA2MCP');FORECAST=ROOT/'reports/observer-predictor-v2-20261103-210801'
sys.path.insert(0,str(FORECAST));import predictor as forecast_code
from .contract import Vocabulary,PLACE_ANCHOR_RELATIVE,VERSION,OBS_VERSION
from .decoder import StructuredPolicy
from .cell_map import MASK_VERSION

class ForecastFeatures:
    """Public player-view projection into the old 84D forecast feature contract.

    Unknown opponent economy/power are zero, not read from hidden house state.
    This is a documented input-domain change from omniscient observer training.
    """
    def __init__(self,spec):
        kinds={'ABSTRACT_TYPE_BUILDINGTYPE':7,'ABSTRACT_TYPE_UNITTYPE':40,'ABSTRACT_TYPE_INFANTRYTYPE':16,'ABSTRACT_TYPE_AIRCRAFTTYPE':3}
        self.catalog={int(ptr):(kinds.get(t['type'],0),int(t.get('cost',0))) for ptr,t in spec.all_types.items()}
        self.history=deque();self.first=None
    def __call__(self,snapshot,observation):
        frame=int(snapshot['frame'])
        if self.first is None:self.first=frame
        own=next((h for h in observation.get('houses',[]) if h.get('current_player')),{});objects=[]
        for entity in snapshot['entities']:
            o=entity['object'];objects.append([1 if entity['friendly'] else 2,o['type_name'],o['health'],*o['cell'],int(bool(o.get('on_map'))),int(bool(o.get('in_limbo'))),0,o['addr'],o['type_ptr']])
        raw={'houses':[[1,'Agent',int(observation.get('money') or 0),int(own.get('power_output') or 0),int(own.get('power_drain') or 0),False],[2,'Opponent',0,0,0,False]],'objects':objects}
        stats,_,_,_=forecast_code.aggregate(raw,self.catalog,set(),ai_count=2)
        normalized=np.log1p(np.maximum(stats,0));normalized[:,-2:]=stats[:,-2:]/155.
        self.history.append((frame,normalized.copy()))
        while len(self.history)>2 and self.history[1][0]<=frame-625:self.history.popleft()
        past,old=self.history[0]
        extra=[(frame-self.first)/67500.,1/6.,min((frame-past)/625.,1.),0.]
        x=np.concatenate([normalized[0],normalized[1],normalized[1],normalized[0]-old[0],extra]).astype(np.float32)
        return x,np.log1p(stats[0,forecast_code.TARGET]).astype(np.float32)

class UnifiedV2Ops(nn.Module):
    def __init__(self,vocabulary,forecast_checkpoint,control_state=None):
        super().__init__();config=forecast_checkpoint['config'];self.vocabulary=vocabulary
        self.forecast=forecast_code.make_model(84);self.forecast.load_state_dict(forecast_checkpoint['state_dict'])
        for parameter in self.forecast.parameters():parameter.requires_grad_(False)
        self.forecast.eval();self.register_buffer('mean',torch.tensor(config['mean'],dtype=torch.float32));self.register_buffer('scale',torch.tensor(config['scale'],dtype=torch.float32));self.register_buffer('delta_scale',torch.tensor(config['delta_scale'],dtype=torch.float32))
        self.control=StructuredPolicy(vocabulary,64);self.control.place_anchor=PLACE_ANCHOR_RELATIVE
        if control_state is not None:self.control.load_state_dict(control_state,strict=True)
        self.fusion=nn.Linear(128,64);nn.init.zeros_(self.fusion.weight);nn.init.zeros_(self.fusion.bias)
    def train(self,mode=True):
        super().train(mode);self.forecast.eval();return self
    def forward(self,snapshots,features,current,h0=None,actions=None,deterministic=True,ablate_forecast=False):
        device=next(self.parameters()).device
        features=torch.as_tensor(np.asarray(features),dtype=torch.float32,device=device).reshape(-1,84)
        current=torch.as_tensor(np.asarray(current),dtype=torch.float32,device=device).reshape(-1,6)
        with torch.no_grad():
            latent=self.forecast.trunk((features-self.mean)/self.scale)
            delta=self.forecast.reg(latent);build=self.forecast.build(latent);risk=self.forecast.death(latent).squeeze(-1)
        base=torch.zeros(len(snapshots),64,device=device) if h0 is None else h0
        conditioned=base if ablate_forecast else base+.1*torch.tanh(self.fusion(latent))
        result=self.control(snapshots,conditioned,actions=actions,deterministic=deterministic)
        result['forecast']=(torch.expm1((current+delta*self.delta_scale).clamp(0,15)),torch.sigmoid(build),torch.sigmoid(risk))
        return result
    def package(self,metadata):
        metadata=dict(metadata)
        profile=getattr(self,'input_profile',None)
        if profile is not None:
            if metadata.get('input_profile',profile)!=profile:raise ValueError('Cannot relabel checkpoint input profile')
            metadata['input_profile']=profile
        return {'schema':'v2-ops-checkpoint/1','action_contract':VERSION,'observation_contract':OBS_VERSION,'place_anchor':PLACE_ANCHOR_RELATIVE,
                'move_anchor':'base-relative-offset/1','vocabulary':self.vocabulary.manifest(),'state_dict':self.state_dict(),
                'metadata':metadata,'legality_mask_version':MASK_VERSION,'forecast_config':{'mean':self.mean.tolist(),'scale':self.scale.tolist(),'delta_scale':self.delta_scale.tolist()}}

def load(path,*,allow_legacy_mask=False):
    checkpoint=torch.load(path,map_location='cpu',weights_only=False)
    if checkpoint.get('schema')!='v2-ops-checkpoint/1' or checkpoint.get('action_contract')!=VERSION:raise ValueError('Not a V2-OPS checkpoint')
    version=checkpoint.get('legality_mask_version')
    if version!=MASK_VERSION and not (allow_legacy_mask and version is None):
        raise ValueError('Mask version mismatch; legacy weight reuse requires explicit opt-in')
    vocab=Vocabulary(checkpoint['vocabulary']['names'])
    base={'config':checkpoint['forecast_config'],'state_dict':{key.removeprefix('forecast.'):value for key,value in checkpoint['state_dict'].items() if key.startswith('forecast.')}}
    model=UnifiedV2Ops(vocab,base);model.load_state_dict(checkpoint['state_dict'],strict=True)
    from .profiles import apply_profile
    apply_profile(model,checkpoint.get('metadata',{}).get('input_profile'))
    return model.eval(),vocab,checkpoint
