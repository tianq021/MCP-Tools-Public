"""Package-relative Feiyu native TTS; reuse the explicitly configured local WSL runtime."""
import argparse,hashlib,json,os,subprocess,sys,time
from pathlib import Path
ROOT=Path(__file__).resolve().parent
def sha(p):
 h=hashlib.sha256()
 with Path(p).open('rb') as f:
  for b in iter(lambda:f.read(8*1024*1024),b''):h.update(b)
 return h.hexdigest()
def load(p):return json.loads(Path(p).read_text(encoding='utf-8-sig'))
def inside(p):
 p=(ROOT/p).resolve();p.relative_to(ROOT);return p
def save(p,x):
 with Path(p).open('x',encoding='utf-8') as f:json.dump(x,f,ensure_ascii=False,indent=2)
def main():
 ap=argparse.ArgumentParser();ap.add_argument('--request',type=Path);ap.add_argument('--verify-only',action='store_true');ap.add_argument('--style',choices=['cute','gentle'],default='cute');args=ap.parse_args()
 assert (ROOT/'runtime.local.json').is_file(), 'Create runtime.local.json from runtime.template.json and configure the WSL paths first'
 manifest=load(ROOT/'manifest.json');runtime=load(ROOT/'runtime.local.json');checks=load(ROOT/'files_sha256.json');assert manifest['schema']=='feiyu-voice-local-release-v1'
 for name,row in checks.items():
  p=inside(name);assert p.is_file() and p.stat().st_size==row['bytes'] and sha(p)==row['sha256'],'Package integrity mismatch: '+name
 repo=Path(runtime['repo']);commit=subprocess.check_output(['git','-C',str(repo),'rev-parse','HEAD'],text=True).strip();assert commit==runtime['commit'];assert not subprocess.check_output(['git','-C',str(repo),'status','--porcelain','--untracked-files=no'],text=True),'Pinned runtime repository has tracked modifications'
 submodules=subprocess.check_output(['git','-C',str(repo),'submodule','status','--recursive'],text=True);assert all(not line or line.startswith(' ') for line in submodules.splitlines()),'Runtime submodule mismatch'
 if args.verify_only:
  print(json.dumps(dict(status='verified',version=manifest['version'],files=len(checks),runtime_repo=str(repo),commit=commit,gpu_loaded=False),ensure_ascii=False));return
 assert args.request,'--request is required';request=load(args.request);style=request.get('style',manifest['default_style']);assert style in manifest['styles'];assert isinstance(request['text'],str) and request['text'].strip()
 selected=manifest['styles'][style];out=Path(request['output_dir']).resolve();assert out!=ROOT
 for name in ['models','references','licenses','validation']:
  protected=ROOT/name;assert out!=protected and not out.is_relative_to(protected),'Output points into release assets'
 assert not out.exists() or (out.is_dir() and not any(out.iterdir())),'Output must be new or empty';out.mkdir(parents=True,exist_ok=True)
 model=inside(manifest['base_model_dir']);checkpoint=inside(selected['llm_checkpoint']);reference=inside(selected['reference_audio']);assert sha(checkpoint)==selected['llm_sha256'] and sha(reference)==selected['reference_sha256']
 params=manifest['inference'];job=dict(schema='feiyu-release-inference-v1',release_version=manifest['version'],speaker_id='feiyu',style=style,candidate=selected['candidate'],text=request['text'],inference=params,manifest_sha256=sha(ROOT/'manifest.json'),file_manifest_sha256=sha(ROOT/'files_sha256.json'),runtime_config_sha256=sha(ROOT/'runtime.local.json'),checkpoint_sha256=sha(checkpoint),reference_sha256=sha(reference),runtime_repo=str(repo),runtime_commit=commit,submodules=submodules,worker_sha256=sha(__file__),runtime_rvc=False)
 save(out/'job.json',job);os.environ.update(HF_HUB_OFFLINE='1',TRANSFORMERS_OFFLINE='1',PYTHONDONTWRITEBYTECODE='1',OMP_NUM_THREADS='4');os.chdir(repo);sys.path.insert(0,str(repo));sys.path.append(str(repo/'third_party/Matcha-TTS'))
 import torch,numpy as np,soundfile as sf
 from cosyvoice.cli.cosyvoice import AutoModel
 from cosyvoice.utils.common import set_all_random_seed
 assert torch.__version__==runtime['torch_version'],'Configured PyTorch version differs from validated runtime';torch.set_num_threads(4);start=time.perf_counter();voice=AutoModel(model_dir=str(model),fp16=False,load_trt=False,load_vllm=False)
 if checkpoint!=model/'llm.pt':
  state=torch.load(checkpoint,map_location='cpu',weights_only=True);voice.model.llm.load_state_dict(state,strict=True);del state
 voice.model.llm.eval();set_all_random_seed(params['seed'])
 with torch.inference_mode():chunks=[x['tts_speech'].detach().cpu().numpy()[0] for x in voice.inference_zero_shot(request['text'],params['prompt_prefix']+selected['reference_text'],str(reference),stream=False,speed=params['speed'],text_frontend=True)]
 assert chunks;audio=np.concatenate(chunks);assert len(audio)>2400 and np.isfinite(audio).all() and abs(audio).max()>1e-4
 raw=out/'native.wav';playback=out/'speech.wav';sf.write(raw,audio,voice.sample_rate,subtype='FLOAT');gain=min(1.,.98/float(abs(audio).max()));sf.write(playback,audio*gain,voice.sample_rate,subtype='PCM_16')
 save(out/'result.json',dict(job,status='success',job_sha256=sha(out/'job.json'),output=str(raw),output_sha256=sha(raw),playback=str(playback),playback_sha256=sha(playback),playback_gain=gain,sample_rate=voice.sample_rate,seconds=len(audio)/voice.sample_rate,peak=float(abs(audio).max()),actual_text_frontend=voice.frontend.text_frontend,elapsed_model_load_and_inference_seconds=time.perf_counter()-start,torch=torch.__version__,cuda=torch.version.cuda))
 print('Feiyu '+style+' ('+selected['candidate']+'): '+str(playback))
if __name__=='__main__':main()
