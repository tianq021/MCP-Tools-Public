"""Input preprocessing selected by checkpoint metadata; no network shape changes."""
import types
import numpy as np

NO_CLOCK = 'state-event-no-clock/1'

def scrub(snapshot, features):
    result = dict(snapshot)
    result['global_features'] = list(snapshot['global_features'])
    result['global_features'][1] = 0.
    x = np.asarray(features, dtype=np.float32).copy()
    if x.shape != (84,):
        raise ValueError('Expected one 84-dimensional forecast feature row')
    x[[80, 82]] = 0.
    return result, x

def apply_profile(model, profile):
    if profile not in (None, NO_CLOCK):
        raise ValueError(f'Unsupported checkpoint input profile: {profile}')
    model.input_profile = profile
    if profile is None:
        return model
    original = model.forward
    def forward(self, snapshots, features, current, *args, **kwargs):
        if len(snapshots) != len(features) or len(snapshots) != len(current):
            raise ValueError('Mismatched snapshot/feature/current batch lengths')
        rows = [scrub(s, x) for s, x in zip(snapshots, features)]
        return original([s for s, _ in rows], [x for _, x in rows], current, *args, **kwargs)
    model.forward = types.MethodType(forward, model)
    return model

def make_features(model, spec):
    from .model import ForecastFeatures
    profile = getattr(model, 'input_profile', None)
    if profile is None:
        return ForecastFeatures(spec)
    if profile != NO_CLOCK:
        raise ValueError(f'Unsupported feature profile: {profile}')
    class StateEventFeatures(ForecastFeatures):
        def __init__(self):
            super().__init__(spec)
            self.last_state = None
        def __call__(self, snapshot, observation):
            x, current = super().__call__(snapshot, observation)
            x[60:80] = 0 if self.last_state is None else x[:20] - self.last_state
            self.last_state = x[:20].copy()
            return scrub(snapshot, x)[1], current
    return StateEventFeatures()

def clean_episode(episode):
    previous = None
    for step in episode['steps']:
        step['snapshot'], step['x'] = scrub(step['snapshot'], step['x'])
        step['x'][60:80] = 0 if previous is None else step['x'][:20] - previous
        previous = step['x'][:20].copy()
    return episode
