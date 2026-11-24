"""Isolated real outpaint probe. Never adopts production engines or changes capability evidence."""
import argparse
import json
from pathlib import Path
import sys
import threading
import time
import uuid

from PIL import Image, ImageDraw

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from app.engine import Engine, CONTEXT_KEYS
from app.image_tools import ImageTools, OutpaintParams, geometry, compose_preserved
from app.seam_blend import blend_outpaint_seams
from app.storage import Store, atomic_json, sha256
from app.resources import resource_snapshot
from app.power import power_snapshot


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--settings',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args()
    args.output.mkdir(parents=True,exist_ok=False)
    report={'kind':'real-native-outpaint-probe','verified':False,'seed':20261024,
            'resources_before':resource_snapshot(),'power':power_snapshot()}
    engine=None
    timer=None
    try:
        if report['power'].get('ac_connected') is not True:
            raise RuntimeError('AC power unavailable or unknown')
        state_dir=Path(__file__).resolve().parents[1]/'.tmp'/('seam-probe-'+uuid.uuid4().hex)
        store=Store(state_dir)
        production=json.loads(args.settings.read_text(encoding='utf-8'))
        settings=store.settings() | {k:production[k] for k in CONTEXT_KEYS if k in production}
        settings.update(keep_loaded=False,battery_protection=True,eager_load=False)
        store.save_settings(settings)
        engine=Engine(store)
        tools=ImageTools(store,engine)
        original=Image.new('RGB',(256,256))
        original.putdata([(70+x//5,100+y//5,145+(x+y)//10) for y in range(256) for x in range(256)])
        draw=ImageDraw.Draw(original)
        draw.rectangle((0,165,255,255),fill=(80,100,70))
        draw.rectangle((72,85,180,190),fill=(176,149,113))
        draw.polygon([(62,85),(126,35),(190,85)],fill=(90,65,60))
        draw.rectangle((110,137,139,190),fill=(74,61,49))
        original.save(args.output/'input.png')
        params=OutpaintParams(extend_left=64,extend_right=64,seam_mode='feather',seed=20261024,
                             prompt='Continue the illustrated landscape naturally on both sides of the house.').model_dump()
        canvas=geometry(original.size,params)
        report['params']=params
        report['canvas']=canvas
        report['admission']=tools.resources_for('outpaint',params,(canvas['width'],canvas['height']))
        if not report['admission']['allowed']:
            raise RuntimeError(report['admission']['reason'])
        job=dict(id=uuid.uuid4().hex,task_type='outpaint',params=params,_settings=settings,
                 _cancel=False,status='running',created=time.time(),started=time.time())
        timer=threading.Timer(600,engine._stop_process)
        timer.daemon=True; timer.start()
        started=time.monotonic()
        with engine.operation_lock:
            candidate,execution=tools.run_outpaint(job,original,canvas)
        report['seconds']=time.monotonic()-started
        candidate.save(args.output/'native-candidate.png')
        strict=compose_preserved(original,candidate,canvas)
        result,diagnostics,_=blend_outpaint_seams(original,candidate.convert('RGB'),canvas,params)
        strict.save(args.output/'strict.png'); result.save(args.output/'feather.png')
        board=Image.new('RGB',(strict.width*2,strict.height+24),'white')
        board.paste(strict,(0,24)); board.paste(result,(strict.width,24))
        d=ImageDraw.Draw(board); d.text((8,5),'STRICT',fill='black'); d.text((strict.width+8,5),'FEATHER',fill='black')
        board.save(args.output/'comparison.png')
        report.update(inference_completed=True,verified=False,quality_status='human review required',
                      seam=diagnostics,input_sha256=sha256(args.output/'input.png'),
                      output_sha256=sha256(args.output/'feather.png'),
                      server_sha256=execution['server_sha256'],models=execution['models'])
    except Exception as exc:
        report.update(inference_completed=False,error=str(exc))
    finally:
        if timer: timer.cancel()
        if engine:
            engine._stop_process()
            engine.close()
        report['resources_after']=resource_snapshot()
        atomic_json(args.output/'probe.json',report)
        print(json.dumps({k:v for k,v in report.items() if k not in ('models',)},ensure_ascii=False))
    return 0 if report.get('inference_completed') else 2


if __name__=='__main__':
    raise SystemExit(main())
