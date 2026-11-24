"""Explicitly versioned production identity/count extension, isolated from old loads.

Uses the existing vocabulary/embedding and current public friendly entities only.
The split linear evaluation is algebraically the same 26-column projection but
retains the legacy 8-column GEMM at initialization, avoiding rounding drift.
"""
import copy
from collections import Counter

import torch
from torch import nn
from torch.nn import functional as F

from scripts.v2_ops.contract import PRODUCTION_DIM
from scripts.v2_ops.decoder import StructuredPolicy
from scripts.v2_ops.profiles import NO_CLOCK, apply_profile
from . import model as attention

SCHEMA = 'v2-ops-board-attention-typed-production/1'
ENCODING = 'typed-count/1'
INPUT_DIM = 26
WEIGHT = 'control.production_encoder.0.weight'
KINDS = ('ABSTRACT_TYPE_BUILDINGTYPE', 'ABSTRACT_TYPE_INFANTRYTYPE', 'ABSTRACT_TYPE_UNITTYPE')


def candidate_type_id(candidate, vocabulary_ids):
    flags = candidate['features'][1:4]
    if len(flags) != 3 or any(v not in (0, 1) for v in flags) or sum(flags) != 1:
        raise ValueError('production candidate must have exactly one known category')
    key = f"{KINDS[flags.index(1)]}:{candidate['name']}"
    if key not in vocabulary_ids:
        raise ValueError(f'unknown production type: {key}')
    return vocabulary_ids[key]


def public_candidate_inputs(snapshot, vocabulary_ids):
    """Return IDs and un-clipped (on-map, not-on-map)/32 counts in candidate order.

    Not-on-map is an observed entity count, not an inferred entire factory queue.
    Address identity prevents double-counting malformed duplicate records.
    """
    placed, pending, seen = Counter(), Counter(), set()
    for entity in snapshot['entities']:
        obj = entity['object']
        if not entity['friendly'] or obj['health'] <= 0:
            continue
        address = obj['addr']
        if address in seen:
            continue
        seen.add(address)
        counts = placed if obj.get('on_map') and not obj.get('in_limbo') else pending
        counts[entity['type_id']] += 1
    ids = [candidate_type_id(c, vocabulary_ids) for c in snapshot['production']]
    return ids, [[placed[i] / 32., pending[i] / 32.] for i in ids]


class TypedPolicy(attention.AttentivePolicy):
    def __init__(self, vocabulary, parent, config):
        StructuredPolicy.__init__(self, vocabulary, parent.hidden)
        self.board_attention = attention.BoardAttention(parent.hidden, config)
        self.load_state_dict(parent.state_dict(), strict=True)
        self.place_anchor = parent.place_anchor
        self.attention_enabled = parent.attention_enabled
        self.production_vocabulary_ids = dict(vocabulary.ids)
        old = self.production_encoder[0]
        extended = nn.Linear(INPUT_DIM, parent.hidden, device=old.weight.device, dtype=old.weight.dtype)
        with torch.no_grad():
            extended.weight.zero_()
            extended.weight[:, :PRODUCTION_DIM].copy_(old.weight)
            extended.bias.copy_(old.bias)
        self.production_encoder[0] = extended

    def encode_production(self, snapshots, features):
        batch, capacity, _ = features.shape
        ids = torch.zeros(batch, capacity, dtype=torch.long, device=features.device)
        counts = features.new_zeros(batch, capacity, 2)
        present = features.new_zeros(batch, capacity, 1)
        for b, snapshot in enumerate(snapshots):
            type_ids, own_counts = public_candidate_inputs(snapshot, self.production_vocabulary_ids)
            n = len(type_ids)
            if n:
                ids[b, :n] = torch.tensor(type_ids, device=features.device)
                counts[b, :n] = features.new_tensor(own_counts)
                present[b, :n] = 1
        extra = torch.cat((self.type_embedding(ids), counts), dim=-1) * present
        linear = self.production_encoder[0]
        original = F.linear(features, linear.weight[:, :PRODUCTION_DIM].contiguous(), linear.bias)
        added = F.linear(extra, linear.weight[:, PRODUCTION_DIM:].contiguous())
        return torch.tanh(original + added)


class TypedModel(attention.AttentionModel):
    def package(self, metadata):
        pack = super().package(metadata)
        pack.update(schema=SCHEMA, production_encoding=ENCODING)
        return pack


def lift(parent):
    """Copy an old attention model with zero new columns; no weights overwritten."""
    if type(parent) is not attention.AttentionModel:
        raise ValueError('lift requires an unextended attention model')
    pack = parent.package({})
    state = pack['state_dict']
    forecast = {k.removeprefix('forecast.'): v for k, v in state.items() if k.startswith('forecast.')}
    model = TypedModel(parent.vocabulary, {'config': pack['forecast_config'], 'state_dict': forecast})
    model.attention_config = parent.attention_config
    model.control = TypedPolicy(parent.vocabulary, parent.control, parent.attention_config)
    extended_state = dict(state)
    extended_state[WEIGHT] = model.control.production_encoder[0].weight.detach().clone()
    model.load_state_dict(extended_state, strict=True)
    return apply_profile(model, NO_CLOCK).eval()


def load(path):
    checkpoint = torch.load(path, map_location='cpu', weights_only=False)
    if checkpoint.get('schema') != SCHEMA or checkpoint.get('production_encoding') != ENCODING:
        raise ValueError('expected a typed-count/1 attention checkpoint')
    weight = checkpoint['state_dict'][WEIGHT]
    if tuple(weight.shape) != (64, INPUT_DIM):
        raise ValueError('typed production weight shape mismatch')
    legacy = dict(checkpoint, schema=attention.SCHEMA, state_dict=dict(checkpoint['state_dict']))
    legacy['state_dict'][WEIGHT] = weight[:, :PRODUCTION_DIM].contiguous()
    parent, vocab, _ = attention._restore(legacy, attention.AttentionConfig(**checkpoint['board_attention']), initializing=False)
    model = lift(parent)
    model.load_state_dict(checkpoint['state_dict'], strict=True)
    return model.eval(), vocab, checkpoint


def migrate_adam(saved, parent, extended):
    """Shape-migrated Adam continuation matched by names from the exact parent.

    Historical trainers store positional optimizer IDs. Resolve these against the
    hash-verified parent's ordered trainable names and validate every tensor shape.
    """
    old = {n: p for n, p in parent.named_parameters() if p.requires_grad}
    new = {n: p for n, p in extended.named_parameters() if p.requires_grad}
    if list(old) != list(new):
        raise ValueError('optimizer parameter name/order mismatch')
    groups = saved['optimizer']['param_groups']
    if len(groups) != 1 or len(groups[0]['params']) != len(old):
        raise ValueError('expected the original one-group Adam layout')
    if saved.get('parameter_names', list(old)) != list(old):
        raise ValueError('saved optimizer names mismatch')
    result = copy.deepcopy(saved)
    for name, pid in zip(old, groups[0]['params']):
        if name != WEIGHT and old[name].shape != new[name].shape:
            raise ValueError(f'unapproved parameter shape change: {name}')
        for key, value in saved['optimizer']['state'].get(pid, {}).items():
            if not torch.is_tensor(value) or value.ndim == 0:
                continue
            if value.shape != old[name].shape:
                raise ValueError(f'optimizer tensor shape mismatch: {name}/{key}')
            if name == WEIGHT:
                migrated = value.new_zeros(new[name].shape)
                migrated[:, :PRODUCTION_DIM].copy_(value)
                result['optimizer']['state'][pid][key] = migrated
    result['parameter_names'] = list(new)
    return result
