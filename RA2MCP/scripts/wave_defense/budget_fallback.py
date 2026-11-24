"""Select another model-ranked product after the existing budget guard vetoes one.

This adapter changes no weights, feature values, recurrent state or native
legality. It filters only already-legal production options through the same
budget classifier; no rejected native command is retried.
"""
import torch
from scripts.v2_ops.contract import validate_action


@torch.inference_mode()
def alternative(model,s,x,current,h_in,h_next,parent,raw,budget,features,db):
    if parent.get('family')!=2:return None,{'reason':'parent_not_production'}
    blocked=[];allowed=[];products=[]
    for p in s['production']:
        permit=bool(p['legal'])
        if permit:
            price=db.get(p['name'],{}).get('price',p['features'][0]*2000)
            permit=budget(features(raw,p['name'],price))=='allow'
            (allowed if permit else blocked).append(p['slot'])
        products.append(dict(p,legal=permit))
    audit=dict(blocked_slots=blocked,allowed_slots=allowed,
        blocked_products=[s['production'][i]['name'] for i in blocked],
        original_parent=parent,mechanism='same neural scores, existing budget guard filters eligible products')
    if parent['production'] not in blocked or not allowed:
        return None,dict(audit,reason='no_budget_approved_alternative')
    # Eligibility is only used at decoding. Encoder feature tensors remain equal.
    filtered=dict(s,production=products)
    result=model([filtered],[x],[current],h_in)
    assert torch.equal(result['h_next'],h_next),'fallback must not alter or advance recurrent state'
    action=result['actions'][0]
    assert action['family']==2 and action['production'] in allowed
    validate_action(s,action)
    return action,dict(audit,selected=action,selected_product=s['production'][action['production']]['name'],
        recurrent_state_unchanged=True)


def install(actor):
    """Install only on the requested live actor; keep old report modules immutable."""
    from shared import old
    economy=actor.parent_actor.economy
    original=economy.decide
    def decide(s,x,current,h,public):
        action,h_next,parent,source=original(s,x,current,h,public)
        if source!='learned_budget_hold':return action,h_next,parent,source
        candidate,audit=alternative(actor.parent,s,x,current,h,h_next,parent,actor.raw,
            lambda f:old.classify(actor.maintenance['budget'],f),old.budget_features,old.DB)
        actor.budget_fallback_notes.append(audit)
        if candidate is None:return action,h_next,parent,source
        return candidate,h_next,parent,'budget_feasible_neural'
    economy.decide=decide
    original_player=actor.decide
    def player(*args,**kwargs):
        actor.budget_fallback_notes=[]
        action,h,parent,source,intent,notes=original_player(*args,**kwargs)
        return action,h,parent,source,intent,dict(notes,budget_fallback=actor.budget_fallback_notes)
    actor.decide=player
