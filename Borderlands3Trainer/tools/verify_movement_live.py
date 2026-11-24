"""Bounded, reversible live verification using dynamic player resolution."""
import json
import math
import struct
import sys
import time
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import memory_scan as m
from runtime_features import FeatureManager


def main():
    root = Path(__file__).resolve().parents[1]
    config = json.loads((root/'trainer_config.json').read_text(encoding='utf-8'))
    manager = FeatureManager(config['expected_executable'], lambda text: print(text,flush=True))
    # Resolve using the same ownership and type checks as the delivered trainer.
    manager.set_movement_multiplier(2)
    speed = manager.movement
    state = dict(speed.active)
    movement = state['identity'][0]
    address = state['address']
    output = Path(__file__).with_name('movement_live_validation.json')
    result = {'pid': manager.pid, 'samples': [], 'restored': False}
    try:
        p = speed.process
        velocity_offset = speed.ref.offset(movement, 'Velocity','StructProperty')
        mode_offset = speed.ref.offset(movement,'MovementMode','ByteProperty')
        def check():
            if speed._target(discover=False)['identity'] != state['identity']:
                raise RuntimeError('Character changed')
        check()
        original = state['original']
        try:
            for multiplier in (1, 2, 3):
                check()
                manager.set_movement_multiplier(multiplier)
                print('MULTIPLIER', multiplier, flush=True)
                for _ in range(40):
                    check()
                    values = struct.unpack('<fff',p.read(movement+velocity_offset,12))
                    scale = struct.unpack('<f',p.read(address,4))[0]
                    result['samples'].append({'multiplier':multiplier,'actual_scale':scale,
                        'speed_xy':math.hypot(*values[:2]),'velocity_z':values[2],
                        'movement_mode':p.read(movement+mode_offset,1)[0]})
                    time.sleep(.25)
        finally:
            manager.set_movement_multiplier(1)
            result['restored'] = p.read(address,4) == original
            output.write_text(json.dumps(result, indent=2),encoding='utf-8')
            print('RESTORED', result['restored'], flush=True)
        for multiplier in (1,2,3):
            samples = [s['speed_xy'] for s in result['samples'] if s['multiplier']==multiplier]
            print(multiplier, 'max_xy', max(samples), 'moving_samples',sum(s>10 for s in samples))
    finally:
        speed.close()


if __name__ == '__main__':
    main()
