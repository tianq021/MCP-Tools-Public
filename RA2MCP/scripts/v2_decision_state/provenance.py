"""Record proposed actions separately from composed actions and native outcomes."""
import copy
import json
import math

SCHEMA = 'decision-provenance/1'


class DecisionTrace:
    def __init__(self):
        self.record = None

    def begin(self, episode, frame, policy_sha256):
        self.record = dict(schema=SCHEMA, episode=episode, frame=frame,
                           policy_sha256=policy_sha256, stages=[], native_outcome=None,
                           outcome_status='NOT_RECORDED', training_ready=False)

    def add(self, stage, action, source, *, intent=None, **details):
        if self.record is None:
            raise RuntimeError('begin a decision before recording stages')
        previous = self.record['stages'][-1]['action'] if self.record['stages'] else None
        item = dict(stage=stage, source=source, action=copy.deepcopy(action),
                    changed_from_previous=None if previous is None else previous != action,
                    intent=copy.deepcopy(intent), details=copy.deepcopy(details))
        # Reject tensors/nonfinite scalars before writing misleading trajectory data.
        json.dumps(item, allow_nan=False)
        self.record['stages'].append(item)

    def neural(self, output):
        if len(output['actions']) != 1:
            raise ValueError('runtime provenance expects one decision')
        logp = float(output['log_prob'][0].detach().cpu())
        if not math.isfinite(logp):
            raise ValueError('nonfinite policy log probability')
        self.add('neural', output['actions'][0], 'neural_parent',
                 log_probability=logp, value=float(output['value'][0].detach().cpu()),
                 decision_mode='deterministic_argmax')

    def result(self):
        if not self.record or not self.record['stages']:
            raise RuntimeError('no decision stages')
        result = copy.deepcopy(self.record)
        result['final_action'] = result['stages'][-1]['action']
        result['overridden_stages'] = [v['stage'] for v in result['stages'][1:]
                                       if v['changed_from_previous']]
        return result

    def attach_outcome(self, outcome):
        self.record['native_outcome'] = copy.deepcopy(outcome)
        self.record['outcome_status'] = 'RECORDED_NOT_PROOF_OF_APPLICATION'
        # Argmax evaluation traces and fixed-controller outputs are not PPO samples.
        return self.result()
