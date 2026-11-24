"""Optional candidate-to-board attention for the existing V2-OPS actor."""
from dataclasses import asdict, dataclass

import torch
from torch import nn
from torch.nn import functional as F

from scripts.v2_ops.cell_map import MASK_VERSION
from scripts.v2_ops.contract import GLOBAL_DIM, OBS_VERSION, PLACE_ANCHOR_RELATIVE, VERSION, Vocabulary
from scripts.v2_ops.decoder import StructuredPolicy
from scripts.v2_ops.model import UnifiedV2Ops
from scripts.v2_ops.profiles import NO_CLOCK, apply_profile

SCHEMA = "v2-ops-board-attention/1"


@dataclass(frozen=True)
class AttentionConfig:
    width: int = 32
    heads: int = 2
    layers: int = 1

    def __post_init__(self):
        if any(type(value) is not int or value <= 0 for value in asdict(self).values()):
            raise ValueError("width, heads and layers must be positive integers")
        if self.width % self.heads:
            raise ValueError("attention width must be divisible by its head count")


class BoardAttention(nn.Module):
    """One global decision query and one query per production candidate."""

    def __init__(self, hidden, config):
        super().__init__()
        self.resources = nn.Linear(GLOBAL_DIM, hidden)
        self.queries = nn.Linear(hidden, config.width)
        self.keys_values = nn.Linear(hidden, config.width)
        self.layers = nn.ModuleList(
            nn.MultiheadAttention(config.width, config.heads, batch_first=True)
            for _ in range(config.layers)
        )
        self.norms = nn.ModuleList(nn.LayerNorm(config.width) for _ in self.layers)
        self.output = nn.Linear(config.width, hidden, bias=False)
        self.use_board_context = True  # paired diagnostic; defaults on at load
        # Start at exactly the legacy actor's distribution. After the first
        # update of this projection, gradients also reach the attention heads.
        nn.init.zeros_(self.output.weight)

    def forward(self, core, entities, production, snapshots):
        counts = [len(snapshot["entities"]) for snapshot in snapshots]
        max_entities = max(counts)
        max_products = max(len(snapshot["production"]) for snapshot in snapshots)
        public = core.new_tensor([snapshot["global_features"] for snapshot in snapshots])
        # The first, always-present token keeps an empty board well-defined.
        board = torch.cat((self.resources(public).unsqueeze(1), entities[:, :max_entities]), dim=1)
        tokens = self.keys_values(board)
        mask = torch.arange(tokens.shape[1], device=core.device)[None, :] > torch.tensor(
            counts, device=core.device
        )[:, None]
        queries = torch.cat((core[:, None], production[:, :max_products] + core[:, None]), dim=1)
        queries = self.queries(queries)
        for layer, norm in zip(self.layers, self.norms):
            if self.use_board_context:
                attended, _ = layer(queries, tokens, tokens, key_padding_mask=mask, need_weights=False)
                queries = queries + attended
            queries = norm(queries)
        residual = self.output(queries)
        return core + residual[:, 0], production + F.pad(
            residual[:, 1:], (0, 0, 0, production.shape[1] - max_products)
        )


class AttentivePolicy(StructuredPolicy):
    def __init__(self, vocabulary, base, config):
        super().__init__(vocabulary, base.hidden)
        self.load_state_dict(base.state_dict(), strict=True)
        self.place_anchor = base.place_anchor
        self.board_attention = BoardAttention(base.hidden, config)
        self.attention_enabled = True

    def encode(self, snapshots, h0=None):
        core, entities, groups, production = super().encode(snapshots, h0)
        if self.attention_enabled:
            core, production = self.board_attention(core, entities, production, snapshots)
        return core, entities, groups, production


class AttentionModel(UnifiedV2Ops):
    def package(self, metadata):
        result = super().package(metadata)
        result.update(schema=SCHEMA, board_attention=asdict(self.attention_config))
        return result


def _restore(checkpoint, config, *, initializing):
    expected = {
        "schema": "v2-ops-checkpoint/1" if initializing else SCHEMA,
        "action_contract": VERSION,
        "observation_contract": OBS_VERSION,
        "legality_mask_version": MASK_VERSION,
        "place_anchor": PLACE_ANCHOR_RELATIVE,
        "move_anchor": "base-relative-offset/1",
    }
    for key, value in expected.items():
        if checkpoint.get(key) != value:
            raise ValueError(f"incompatible {key} for attention actor")
    if checkpoint["metadata"].get("input_profile") != NO_CLOCK:
        raise ValueError("expected public state-event-no-clock profile")
    vocab = Vocabulary(checkpoint["vocabulary"]["names"])
    if vocab.manifest() != checkpoint["vocabulary"]:
        raise ValueError("checkpoint vocabulary mismatch")
    state = checkpoint["state_dict"]
    forecast = {key.removeprefix("forecast."): value for key, value in state.items()
                if key.startswith("forecast.")}
    model = AttentionModel(vocab, {"config": checkpoint["forecast_config"], "state_dict": forecast})
    model.attention_config = config
    if initializing:
        model.load_state_dict(state, strict=True)
    model.control = AttentivePolicy(vocab, model.control, config)
    if not initializing:
        model.load_state_dict(state, strict=True)
    return apply_profile(model, NO_CLOCK).eval(), vocab, checkpoint


def from_baseline(path, config=AttentionConfig()):
    checkpoint = torch.load(path, map_location="cpu", weights_only=False)
    return _restore(checkpoint, config, initializing=True)


def load(path):
    """Load a trained attention checkpoint, including its exact width/heads/depth."""
    checkpoint = torch.load(path, map_location="cpu", weights_only=False)
    if checkpoint.get("schema") != SCHEMA:
        raise ValueError("expected a V2-OPS board-attention checkpoint")
    return _restore(checkpoint, AttentionConfig(**checkpoint["board_attention"]), initializing=False)
