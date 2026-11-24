"""Small entity/group encoder + GRUCell + autoregressive conditional heads.

Only active factors enter log_prob. No optimizer, rollout, PPO or training is
started by importing or constructing this model. Batch elements may differ in
active factor paths. Coordinate y is explicitly conditioned on sampled x.
"""
from __future__ import annotations
import math
import torch
from torch import nn
from torch.distributions import Categorical
from .contract import (VERSION,OBS_VERSION,ENTITY_DIM,GLOBAL_DIM,PRODUCTION_DIM,
                       N_ENTITY,N_GROUP,N_TYPE,factors,mask,validate_action,
                       PLACE_ANCHOR,PLACE_ANCHOR_ABSOLUTE)


class StructuredPolicy(nn.Module):
    def __init__(self,vocabulary,hidden=64):
        super().__init__()
        self.vocabulary_sha256=vocabulary.sha256
        self.place_anchor=PLACE_ANCHOR
        self.hidden=hidden
        self.type_embedding=nn.Embedding(len(vocabulary.names)+1,16)
        self.entity_encoder=nn.Sequential(nn.Linear(ENTITY_DIM+16,hidden),nn.Tanh())
        self.production_encoder=nn.Sequential(nn.Linear(PRODUCTION_DIM,hidden),nn.Tanh())
        self.core=nn.GRUCell(hidden*2+GLOBAL_DIM,hidden)
        self.family_head=nn.Linear(hidden,7)
        self.family_embedding=nn.Embedding(7,hidden)
        self.group_embedding=nn.Embedding(8,hidden)
        self.x_embedding=nn.Embedding(256,hidden)
        self.pending_embedding=nn.Embedding(4,hidden)
        self.query=nn.ModuleDict({k:nn.Linear(hidden,hidden) for k in ('group','target','production')})
        self.pending_head=nn.Linear(hidden,4)
        self.x_head=nn.Linear(hidden,256)
        self.y_head=nn.Linear(hidden,256)
        self.value_head=nn.Linear(hidden,1)

    @property
    def device(self):return self.family_head.weight.device

    def encode(self,snapshots,h0=None):
        if not snapshots:raise ValueError('empty batch')
        batch=len(snapshots);device=self.device
        features=torch.zeros(batch,256,ENTITY_DIM,device=device)
        type_ids=torch.zeros(batch,256,dtype=torch.long,device=device)
        present=torch.zeros(batch,256,dtype=torch.bool,device=device)
        membership=torch.zeros(batch,8,256,device=device)
        prod_features=torch.zeros(batch,96,PRODUCTION_DIM,device=device)
        prod_present=torch.zeros(batch,96,device=device)
        for b,s in enumerate(snapshots):
            if s['schema']!=OBS_VERSION or s['action_contract']!=VERSION or s['vocabulary_sha256']!=self.vocabulary_sha256:
                raise ValueError('observation/action/vocabulary contract mismatch')
            n=len(s['entities'])
            if n:
                features[b,:n]=torch.tensor([e['features'] for e in s['entities']],device=device)
                type_ids[b,:n]=torch.tensor([e['type_id'] for e in s['entities']],device=device)
                present[b,:n]=True
            slots={e['track_id']:i for i,e in enumerate(s['entities'])}
            for g,members in enumerate(s['groups']):
                for member in members:membership[b,g,slots[member['track_id']]]=1
            p=len(s['production'])
            if p:
                prod_features[b,:p]=torch.tensor([t['features'] for t in s['production']],device=device)
                prod_present[b,:p]=1
        entity=self.entity_encoder(torch.cat([features,self.type_embedding(type_ids)],dim=-1))
        groups=torch.bmm(membership,entity)/membership.sum(-1,keepdim=True).clamp_min(1)
        entity_pool=(entity*present.unsqueeze(-1)).sum(1)/present.sum(1,keepdim=True).clamp_min(1)
        production=self.production_encoder(prod_features)
        production_pool=(production*prod_present.unsqueeze(-1)).sum(1)/prod_present.sum(1,keepdim=True).clamp_min(1)
        globals_=torch.tensor([s['global_features'] for s in snapshots],dtype=torch.float32,device=device)
        if h0 is None:h0=torch.zeros(batch,self.hidden,device=device)
        if h0.shape!=(batch,self.hidden):raise ValueError('wrong recurrent state shape')
        core=self.core(torch.cat([entity_pool,production_pool,globals_],-1),h0)
        return core,entity,groups,production

    def forward(self,snapshots,h0=None,actions=None,deterministic=False):
        if actions is not None and len(actions)!=len(snapshots):raise ValueError('action batch mismatch')
        core,entity,groups,production=self.encode(snapshots,h0)
        outputs=[];joint=[];all_parts=[]
        for b,s in enumerate(snapshots):
            supplied=actions[b] if actions is not None else None
            if supplied is not None:validate_action(s,supplied)
            chosen={};parts={};q=core[b]
            def pick(name,logits):
                legal=torch.tensor(mask(s,name,chosen),dtype=torch.bool,device=self.device)
                if not legal.any():raise ValueError(f'empty conditional mask: {name}')
                if logits.shape!=legal.shape:raise ValueError(f'head/mask mismatch: {name}')
                distribution=Categorical(logits=logits.masked_fill(~legal,float('-inf')))
                value=(torch.tensor(supplied[name],device=self.device) if supplied is not None else
                       logits.masked_fill(~legal,float('-inf')).argmax() if deterministic else distribution.sample())
                chosen[name]=int(value.item())
                parts[name]=distribution.log_prob(value)
                return chosen[name]
            f=pick('family',self.family_head(q));q=q+self.family_embedding.weight[f]
            for name in factors(f):
                if name=='group':
                    v=pick(name,groups[b]@self.query[name](q)/math.sqrt(self.hidden))
                    q=q+groups[b,v]+self.group_embedding.weight[v]
                elif name=='target':
                    scores=entity[b]@self.query[name](q)/math.sqrt(self.hidden)
                    pick(name,torch.cat([scores,scores.new_zeros(1)]))
                elif name=='production':pick(name,production[b]@self.query[name](q)/math.sqrt(self.hidden))
                elif name=='pending':
                    v=pick(name,self.pending_head(q));q=q+self.pending_embedding.weight[v]
                elif name=='x':
                    v=pick(name,self.x_head(q));q=q+self.x_embedding.weight[v]
                elif name=='y':pick(name,self.y_head(q))
            outputs.append(chosen);all_parts.append(parts);joint.append(torch.stack(list(parts.values())).sum())
        return {'actions':outputs,'log_prob':torch.stack(joint),'factor_log_probs':all_parts,
                'h_next':core,'value':self.value_head(core).squeeze(-1)}

    def checkpoint(self,vocabulary):
        if vocabulary.sha256!=self.vocabulary_sha256:raise ValueError('wrong vocabulary')
        return {'schema':'structured-policy-checkpoint/1','action_contract':VERSION,'observation_contract':OBS_VERSION,
            'vocabulary':vocabulary.manifest(),'hidden':self.hidden,'state_dict':self.state_dict(),
            'place_anchor':self.place_anchor,
            'status':'UNTRAINED_INITIALIZATION','compatible_with_301':False}


def load_checkpoint(path,vocabulary,device='cpu',expect_place_anchor=None):
    checkpoint=torch.load(path,map_location=device,weights_only=True)
    if (checkpoint.get('schema')!='structured-policy-checkpoint/1' or checkpoint.get('action_contract')!=VERSION
        or checkpoint.get('observation_contract')!=OBS_VERSION or checkpoint['vocabulary']!=vocabulary.manifest()):
        raise ValueError('checkpoint contract/vocabulary mismatch')
    mode=checkpoint.get('place_anchor',PLACE_ANCHOR_ABSOLUTE)
    if expect_place_anchor is not None and mode!=expect_place_anchor:
        raise ValueError(f'place_anchor mismatch: checkpoint {mode} != expected {expect_place_anchor}')
    model=StructuredPolicy(vocabulary,checkpoint['hidden']).to(device)
    model.load_state_dict(checkpoint['state_dict'],strict=True)
    model.place_anchor=mode
    return model
