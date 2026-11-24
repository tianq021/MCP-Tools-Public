"""Bounded live read/write/restore validation, not a drop-rate statistical test."""
import json
import sys
import time
from pathlib import Path
root=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(root))
from runtime_features import FeatureManager

config=json.loads((root/'trainer_config.json').read_text(encoding='utf-8'))
manager=FeatureManager(config['expected_executable'],lambda text:print(text,flush=True))
report={'statistical_drop_test':False}
try:
    report['before']=manager.read_loot()
    print('BEFORE',report['before'],flush=True)
    report['applied']=manager.set_loot(5,10)
    print('APPLIED',report['applied'],flush=True)
    time.sleep(2)
    report['after_delay']=manager.read_loot()
    if report['after_delay']['scales']['VeryRare']!=5 or report['after_delay']['scales']['Legendary']!=10:
        raise RuntimeError('Game did not retain the requested scales')
    report['restore']=manager.restore_loot()
    report['after_restore']=manager.read_loot()
    report['restored_verified']=report['before']['scales']==report['after_restore']['scales']
    if not report['restored_verified']:raise RuntimeError('Restore mismatch')
    print('RESTORED',report['restored_verified'],flush=True)
finally:
    try:
        if manager.loot:manager.loot.close()
    finally:
        (root/'tools'/'loot_integration_validation.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
