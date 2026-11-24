"""Recurrent PPO for the existing conditional V2-OPS policy (no new heads).

Each rollout is one uninterrupted episode prefix from a frozen policy. Replay
keeps the entire prefix in order, including its recurrent state; no frame shuffle.
Only native feedback accepted by the collector is admitted by the run driver.
"""
import copy
import hashlib
import math
import random
from pathlib import Path

import numpy as np
import torch

from .model import load


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def advantages(rewards, values, terminated, bootstrap, gamma=.99, lam=.95):
    """One episode only. A truncation bootstraps, a verified terminal does not."""
    n = len(rewards)
    if not n or len(values) != n or len(terminated) != n:
        raise ValueError('inconsistent rollout lengths')
    if any(terminated[:-1]):
        raise ValueError('episode boundary inside recurrent sequence')
    if not all(math.isfinite(float(x)) for x in [*rewards, *values, bootstrap]):
        raise ValueError('non-finite rollout')
    out = np.zeros(n, dtype=np.float32)
    carry = 0.
    next_value = float(bootstrap)
    for t in range(n - 1, -1, -1):
        live = 0. if terminated[t] else 1.
        delta = rewards[t] + gamma * next_value * live - values[t]
        carry = delta + gamma * lam * live * carry
        out[t] = carry
        next_value = values[t]
    return out, out + np.asarray(values, dtype=np.float32)


def optimizer_for(model, config):
    return torch.optim.Adam([p for p in model.parameters() if p.requires_grad], lr=config['lr'])


def replay(model, rollout, entropy=False):
    h = torch.tensor(rollout['h0'], dtype=torch.float32).reshape(1, 64)
    lp, values, ent_grad, ent_value, hidden = [], [], [], [], []
    for row in rollout['steps']:
        args = ([row['snapshot']], [row['features']], [row['current']])
        out = model(*args, h, actions=[row['action']])
        lp.append(out['log_prob'][0]); values.append(out['value'][0])
        if entropy:
            # Fresh current-policy action, NOT the old rollout action. The
            # score-function derivative of E[log p(a)] is (log p(a)+1)dlog p(a).
            # Summing conditional log probs covers the full variable-length joint.
            sampled_lp = model(*args, h, deterministic=False)['log_prob'][0]
            ent_grad.append(sampled_lp * (sampled_lp.detach() + 1.))
            ent_value.append(-sampled_lp.detach())
        h = out['h_next']; hidden.append(h)
    return {'log_prob': torch.stack(lp), 'value': torch.stack(values),
            'entropy_surrogate': torch.stack(ent_grad) if entropy else None,
            'entropy_estimate': torch.stack(ent_value) if entropy else None,
            'hidden': hidden}


def check_rollout(rollout, policy_sha, *, native=True):
    if rollout['policy_sha256'] != policy_sha:
        raise ValueError('stale/off-policy rollout')
    if native and (rollout.get('provenance') != 'NATIVE_MODEL_ONLY' or
                   not rollout.get('feedback_valid') or rollout.get('mode') != 'collect'):
        raise ValueError('non-native or unverified rollout')
    if rollout['end_kind'] not in ('collection_cutoff', 'timeout', 'win', 'defeat'):
        raise ValueError('unverified outcome cannot enter training')
    if not rollout['steps'] or any(rollout['h0']):
        raise ValueError('pilot requires a nonempty episode prefix from reset')
    previous = None
    for i, row in enumerate(rollout['steps']):
        if row.get('feedback_valid') is not True or row['execution']['fatal']:
            raise ValueError('invalid transition')
        frame = row['snapshot']['frame']
        if row['next_frame'] <= frame or (previous is not None and frame != previous):
            raise ValueError('noncontiguous state sequence')
        if row['snapshot']['episode'] != rollout['episode_id']:
            raise ValueError('mixed episodes')
        if row['terminated'] and i != len(rollout['steps']) - 1:
            raise ValueError('transition after terminal')
        if abs(sum(row['reward_parts'].values()) - row['reward']) > 1e-8:
            raise ValueError('reward accounting mismatch')
        previous = row['next_frame']
    last = rollout['steps'][-1]
    if last['terminated'] != (rollout['end_kind'] in ('win', 'defeat')):
        raise ValueError('terminal classification mismatch')
    if last['terminated'] and rollout['bootstrap_value'] != 0:
        raise ValueError('terminal must have zero bootstrap')
    if not last['terminated'] and rollout['bootstrap_snapshot']['frame'] != previous:
        raise ValueError('bootstrap must use actual final next state')


def update(model, optimizer, rollouts, config, policy_sha, *, native=True):
    """One frozen collection wave -> ordered BPTT PPO epochs -> one new policy."""
    total = sum(len(r['steps']) for r in rollouts)
    if total < 2 or total > config['batch_size']:
        raise ValueError('pilot uses one bounded minibatch of complete prefixes')
    all_adv, all_returns = [], []
    max_lp_error = max_value_error = max_hidden_error = 0.
    for rollout in rollouts:
        check_rollout(rollout, policy_sha, native=native)
        rows = rollout['steps']
        a, ret = advantages([r['reward'] for r in rows], [r['old_value'] for r in rows],
                            [r['terminated'] for r in rows], rollout['bootstrap_value'],
                            config['gamma'], config['gae_lambda'])
        all_adv.extend(a); all_returns.extend(ret)
        with torch.no_grad():
            fresh = replay(model, rollout)
            max_lp_error = max(max_lp_error, float((fresh['log_prob'] - torch.tensor([r['old_log_prob'] for r in rows])).abs().max()))
            max_value_error = max(max_value_error, float((fresh['value'] - torch.tensor([r['old_value'] for r in rows])).abs().max()))
            for h, row in zip(fresh['hidden'], rows):
                max_hidden_error = max(max_hidden_error, float((h - torch.tensor(row['h_next'])).abs().max()))
            if not rows[-1]['terminated'] and native:
                boot = model([rollout['bootstrap_snapshot']], [rollout['bootstrap_features']],
                             [rollout['bootstrap_current']], fresh['hidden'][-1])['value'][0]
                max_value_error = max(max_value_error, abs(float(boot) - rollout['bootstrap_value']))
    if max(max_lp_error, max_value_error, max_hidden_error) > 2e-5:
        raise ValueError(f'collection/replay mismatch: {max_lp_error}, {max_value_error}, {max_hidden_error}')
    adv = torch.tensor(np.asarray(all_adv), dtype=torch.float32)
    ret = torch.tensor(np.asarray(all_returns), dtype=torch.float32)
    adv = (adv - adv.mean()) / (adv.std() + 1e-8)
    old_lp = torch.tensor([s['old_log_prob'] for r in rollouts for s in r['steps']])
    before = {k: v.detach().clone() for k, v in model.state_dict().items()}
    epochs = []
    model.train()
    for epoch in range(config['n_epochs']):
        outputs = [replay(model, r, entropy=True) for r in rollouts]
        lp = torch.cat([o['log_prob'] for o in outputs])
        value = torch.cat([o['value'] for o in outputs])
        log_ratio = lp - old_lp
        ratio = log_ratio.exp()
        policy_loss = -torch.minimum(ratio * adv, ratio.clamp(1-config['clip_range'], 1+config['clip_range']) * adv).mean()
        value_loss = ((value - ret) ** 2).mean()
        entropy_surrogate = torch.cat([o['entropy_surrogate'] for o in outputs]).mean()
        loss = policy_loss + config['vf_coef'] * value_loss + config['ent_coef'] * entropy_surrogate
        if not torch.isfinite(loss):
            raise ValueError('non-finite PPO loss')
        optimizer.zero_grad(set_to_none=True)
        loss.backward()
        norm = torch.nn.utils.clip_grad_norm_([p for p in model.parameters() if p.requires_grad], config['max_grad_norm'], error_if_nonfinite=True)
        optimizer.step()
        epochs.append({'epoch': epoch + 1, 'policy_loss': float(policy_loss.detach()),
                       'value_loss': float(value_loss.detach()), 'grad_norm_before_clip': float(norm),
                       'entropy_estimate': float(torch.cat([o['entropy_estimate'] for o in outputs]).mean()),
                       'approx_kl': float(((ratio - 1) - log_ratio).mean().detach()),
                       'clip_fraction': float(((ratio - 1).abs() > config['clip_range']).float().mean())})
    model.eval()
    changed = [k for k, v in model.state_dict().items() if not torch.equal(v, before[k])]
    if not changed or any(k.startswith('forecast.') for k in changed):
        raise ValueError('weights unchanged or frozen forecast changed')
    return {'transitions': total, 'optimizer_steps': len(epochs), 'epochs': epochs,
            'initial_log_prob_max_error': max_lp_error, 'initial_value_max_error': max_value_error,
            'initial_hidden_max_error': max_hidden_error, 'changed_tensors': changed,
            'advantage_raw_mean': float(np.mean(all_adv)), 'return_mean': float(ret.mean()),
            'frozen_forecast_unchanged': True}


def save_resume(model, optimizer, directory, config, progress):
    directory = Path(directory); directory.mkdir(exist_ok=False)
    policy = directory / 'policy.pt'
    torch.save(model.package({'status': 'EXPERIMENTAL_RL_NOT_PROMOTED', 'algorithm': 'recurrent-conditional-ppo/1', **progress}), policy)
    torch.save({'schema': 'v2-ops-ppo-resume/1', 'policy_sha256': sha(policy),
                'optimizer': optimizer.state_dict(), 'torch_rng': torch.get_rng_state(),
                'numpy_rng': np.random.get_state(), 'python_rng': random.getstate(),
                'config': copy.deepcopy(config), 'progress': progress}, directory / 'trainer.pt')
    return policy


def load_resume(directory, expected_config=None):
    directory = Path(directory)
    state = torch.load(directory / 'trainer.pt', map_location='cpu', weights_only=False)
    if state['schema'] != 'v2-ops-ppo-resume/1' or sha(directory/'policy.pt') != state['policy_sha256']:
        raise ValueError('resume state/policy mismatch')
    if expected_config is not None and state['config'] != expected_config:
        raise ValueError('resume configuration changed')
    model, _, _ = load(directory/'policy.pt')
    optimizer = optimizer_for(model, state['config']); optimizer.load_state_dict(state['optimizer'])
    torch.set_rng_state(state['torch_rng']); np.random.set_state(state['numpy_rng']); random.setstate(state['python_rng'])
    return model, optimizer, state
