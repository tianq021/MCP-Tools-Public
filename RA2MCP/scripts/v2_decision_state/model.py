"""Identity-indexed catalog availability for the recurrent decision core.

Consumes existing public snapshot fields; the native action contract is unchanged.
New weights start at zero, preserving the parent policy before any updates.
"""
import copy

import torch
from torch import nn

from scripts.v2_attention import model as attention
from scripts.v2_ops.decoder import StructuredPolicy
from scripts.v2_ops.profiles import NO_CLOCK, apply_profile

SCHEMA = 'v2-ops-decision-state/1'
ENCODING = 'vocabulary-catalog-presence-and-legal/1'
KINDS = ('ABSTRACT_TYPE_BUILDINGTYPE', 'ABSTRACT_TYPE_INFANTRYTYPE', 'ABSTRACT_TYPE_UNITTYPE')


class AvailabilityPolicy(attention.AttentivePolicy):
    def __init__(self, vocabulary, base, blind=False):
        StructuredPolicy.__init__(self, vocabulary, base.hidden)
        self.board_attention = copy.deepcopy(base.board_attention)
        self.attention_enabled = base.attention_enabled
        self.place_anchor = base.place_anchor
        self.load_state_dict(base.state_dict(), strict=True)
        self.product_vocabulary = dict(vocabulary.ids)
        self.availability_blind = bool(blind)
        self.availability_size = len(vocabulary.names) + 1
        self.availability_projection = nn.Linear(2 * self.availability_size, self.hidden, bias=False)
        nn.init.zeros_(self.availability_projection.weight)

    def availability_features(self, snapshots):
        result = self.family_head.weight.new_zeros(len(snapshots), 2 * self.availability_size)
        for row, snapshot in enumerate(snapshots):
            found = set()
            for product in snapshot['production']:
                flags = product['features'][1:4]
                if flags.count(1.0) != 1 or any(value not in (0, 1) for value in flags):
                    raise ValueError('ambiguous product kind')
                identity = f"{KINDS[flags.index(1.0)]}:{product['name']}"
                index = self.product_vocabulary.get(identity)
                if index is None or index in found:
                    raise ValueError(f'unknown or duplicate production identity: {identity}')
                if type(product['legal']) is not bool:
                    raise ValueError('production legality must be a bool')
                found.add(index)
                result[row, index] = 1
                if not self.availability_blind:
                    result[row, self.availability_size + index] = float(product['legal'])
        return result

    def encode(self, snapshots, h0=None):
        core, entities, groups, production = super().encode(snapshots, h0)
        core = core + torch.tanh(self.availability_projection(self.availability_features(snapshots)))
        # The returned core drives family/conditional heads AND next-step memory.
        return core, entities, groups, production


class AvailabilityModel(attention.AttentionModel):
    def package(self, metadata):
        result = super().package(metadata)
        result.update(schema=SCHEMA, decision_state={
            'encoding': ENCODING,
            'blind': self.control.availability_blind,
            'vocabulary_sha256': self.vocabulary.sha256,
        })
        return result


def _from_attention(base, vocabulary, checkpoint, blind):
    forecast = {key.removeprefix('forecast.'): value for key, value in checkpoint['state_dict'].items()
                if key.startswith('forecast.')}
    model = AvailabilityModel(vocabulary, {'config': checkpoint['forecast_config'], 'state_dict': forecast})
    model.attention_config = base.attention_config
    model.control = AvailabilityPolicy(vocabulary, base.control, blind)
    model.fusion.load_state_dict(base.fusion.state_dict(), strict=True)
    return apply_profile(model, NO_CLOCK).eval()


def from_baseline(path, *, blind=False):
    base, vocabulary, checkpoint = attention.load(path)
    return _from_attention(base, vocabulary, checkpoint, blind), vocabulary, checkpoint


def load(path):
    checkpoint = torch.load(path, map_location='cpu', weights_only=False)
    if checkpoint.get('schema') != SCHEMA:
        raise ValueError('expected a decision-state checkpoint')
    config = checkpoint.get('decision_state', {})
    if config.get('encoding') != ENCODING or type(config.get('blind')) is not bool:
        raise ValueError('unsupported decision-state encoding')
    if config.get('vocabulary_sha256') != checkpoint['vocabulary']['sha256']:
        raise ValueError('decision-state vocabulary mismatch')
    legacy = dict(checkpoint, schema=attention.SCHEMA, state_dict={
        key: value for key, value in checkpoint['state_dict'].items()
        if key != 'control.availability_projection.weight'})
    base, vocabulary, _ = attention._restore(
        legacy, attention.AttentionConfig(**checkpoint['board_attention']), initializing=False)
    model = _from_attention(base, vocabulary, legacy, config['blind'])
    model.load_state_dict(checkpoint['state_dict'], strict=True)
    return model.eval(), vocabulary, checkpoint
