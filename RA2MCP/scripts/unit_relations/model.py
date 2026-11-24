"""Standalone mechanic pretraining; never substitutes a live action policy."""
from __future__ import annotations
import torch
from torch import nn

SCHEMA = 'unit-relations-mechanics/1'


class UnitRelations(nn.Module):
    def __init__(self, input_dim, variant='transformer', width=64, heads=4, layers=2):
        super().__init__()
        if variant not in ('transformer', 'mlp'): raise ValueError(variant)
        self.config = dict(input_dim=input_dim, variant=variant, width=width, heads=heads, layers=layers)
        self.embed = nn.Sequential(nn.Linear(input_dim, width), nn.GELU(), nn.LayerNorm(width))
        if variant == 'transformer':
            layer = nn.TransformerEncoderLayer(width, heads, width*2, dropout=0.,
                        activation='gelu', batch_first=True, norm_first=True)
            self.relations = nn.TransformerEncoder(layer, layers, enable_nested_tensor=False)
        else:
            # Same per-token depth without attention. Parameter counts are reported.
            self.relations = nn.Sequential(*[nn.Sequential(nn.Linear(width,width*2),
                nn.GELU(),nn.Linear(width*2,width),nn.LayerNorm(width)) for _ in range(layers)])
        self.pair = nn.Sequential(nn.Linear(width*4,128),nn.GELU(),nn.Linear(128,64),nn.GELU(),nn.Linear(64,4))

    def forward(self,x):
        if x.ndim != 3 or x.shape[1] != 2: raise ValueError('expected two homogeneous-squad tokens')
        z=self.relations(self.embed(x));a,b=z[:,0],z[:,1]
        left=self.pair(torch.cat((a,b,a-b,a*b),-1))
        right=self.pair(torch.cat((b,a,b-a,b*a),-1))
        y=torch.stack((left,right),1)
        return dict(compatible=y[:,:,0], in_range=y[:,:,1],
                    log_dpf=torch.nn.functional.softplus(y[:,:,2]),
                    pressure=torch.tanh((left[:,3]-right[:,3])/2))

    def package(self,metadata):
        return dict(schema=SCHEMA,config=self.config,state_dict=self.state_dict(),metadata=metadata)


def load(path,device='cpu'):
    p=torch.load(path,map_location=device,weights_only=False)
    if p.get('schema')!=SCHEMA: raise ValueError('wrong unit-relation schema')
    model=UnitRelations(**p['config']).to(device)
    model.load_state_dict(p['state_dict'],strict=True)
    return model.eval(),p
