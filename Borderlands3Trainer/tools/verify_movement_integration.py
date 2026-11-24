"""Exercise shipped dynamic locator and 2 -> 3 -> original restoration in-game."""
import json
import struct
import sys
import time
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from runtime_features import FeatureManager

root = Path(__file__).resolve().parents[1]
config = json.loads((root/'trainer_config.json').read_text(encoding='utf-8'))
manager = FeatureManager(config['expected_executable'], lambda text: print(text,flush=True))
report = {'records': []}
try:
    for multiplier in (2,3):
        record = manager.set_movement_multiplier(multiplier)
        report['records'].append(record)
        print(record, flush=True)
        time.sleep(2)
        message = manager.check_movement()
        if message:
            raise RuntimeError(message)
    state = dict(manager.movement.active)
    report['restore'] = manager.set_movement_multiplier(1)
    report['restored_bytes_verified'] = manager.movement.process.read(state['address'],4) == state['original']
    if not report['restored_bytes_verified']:
        raise RuntimeError('restore verification failed')
    print(report,flush=True)
finally:
    manager.restore_all()
    if manager.movement:
        manager.movement.close()
    (root/'tools'/'movement_integration_validation.json').write_text(json.dumps(report,indent=2,ensure_ascii=False),encoding='utf-8')
