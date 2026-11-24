"""Read intact serial episodes for future BC review; no training or row splits."""
import json
import pathlib
from .contract import VERSION,OBS_VERSION
from .data import verify
from dual_channel.data import sha,write_json


def read_episode(run,vocabulary):
    run=pathlib.Path(run);out=run/'structured';check=verify(out)
    summary=json.loads((out/'summary.json').read_text(encoding='utf-8'))
    if not check['pass'] or not summary['capture_complete'] or summary['failure']:
        raise ValueError('intact completed serial capture required')
    rows=[json.loads(l) for l in (out/'transitions.jsonl').read_text(encoding='utf-8').splitlines() if l.strip()]
    episodes={r['snapshot']['episode'] for r in rows}
    if len(episodes)!=1:raise ValueError('one episode required; split only by whole episodes')
    for i,r in enumerate(rows):
        s=r['snapshot']
        if s['vocabulary_sha256']!=vocabulary.sha256 or s['action_contract']!=VERSION or s['schema']!=OBS_VERSION:
            raise ValueError('reader contract/vocabulary mismatch')
        if r['recurrent']['reset']!=(i==0):raise ValueError('incorrect recurrent episode boundary')
    # Rejected commands still belong to recurrent history, but never to label loss.
    # Placement anchors resolved by native PlaceQuery need separate reviewed semantics.
    return {'episode':next(iter(episodes)),'snapshots':[r['snapshot'] for r in rows],
        'actions':[r['action'] for r in rows],
        'label_candidate_mask':[r['execution']['classification']=='ACCEPTED' and r['action']['family']!=3 for r in rows],
        'decision_ids':[r['decision_id'] for r in rows],
        'h0':'ZERO_AT_EPISODE_START; carry or recompute real current-policy history; no fabricated mid-chunk state',
        'formal_training_eligible':False,'split':'UNASSIGNED_WHOLE_EPISODE','verification':check}


def check_partitions(partitions):
    seen=set()
    for name,episodes in partitions.items():
        if len(episodes)!=len(set(episodes)) or seen.intersection(episodes):raise ValueError('episode leakage/duplicate')
        seen.update(episodes)
    return True


def export_review_index(run,out,vocabulary):
    run,out=pathlib.Path(run),pathlib.Path(out)
    if out.exists():raise FileExistsError('new review index required')
    episode=read_episode(run,vocabulary)
    write_json(out,{'schema':'structured-bc-review-index/1','action_contract':VERSION,'observation_contract':OBS_VERSION,
        'source_run':str(run),'source_sha256':sha(run/'structured/transitions.jsonl'),
        'vocabulary_sha256':vocabulary.sha256,'episode':episode['episode'],
        'decisions':len(episode['actions']),'label_candidates':sum(episode['label_candidate_mask']),
        'decision_ids':episode['decision_ids'],'label_candidate_mask':episode['label_candidate_mask'],
        'formal_training_eligible':False,'split':episode['split'],'h0':episode['h0'],
        'limitations':['Teacher strength UNKNOWN','No independent validation episode yet',
                      'NOOP and rejected/unknown actions excluded from label loss; retain history',
                      'PLACE anchors excluded pending resolution review','No PPO actor admission or fake301/1801 conversion']})
