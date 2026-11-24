"""Pure planning/config validation and Windows process ownership for fixed evaluation."""
from __future__ import annotations
from contextlib import AbstractContextManager
import hashlib
import json
import math
import os
from pathlib import Path
import re
import shutil
import socket
import subprocess
import time

SCHEMA = 'v22-fixed-per-case-v2'
PROTOCOL = {'version': 'v2.2-fixed-eval-2', 'map': '2peaks', 'difficulty': 'easy',
            'credits': 20000, 'units': 8, 'superweapons': False,
            'decision_seconds': .5, 'control_mode': 'full','inference_threads':1,
            'action_space_version':'full_ground_v2p1','target_candidate_range':30.,'target_nearest_k':3,
            'render_off':True}
REQUIRED = {('settings','uimapname'):'2peaks', ('settings','aidifficulty'):'2',
            ('settings','credits'):'20000', ('settings','unitcount'):'8',
            ('settings','superweapons'):'no', ('settings','gamespeed'):'1',
            ('settings','scenario'):'spawnmap.ini', ('settings','playercount'):'2',
            ('settings','aiplayers'):'1', ('househandicaps','multi2'):'2'}


class EvaluationError(RuntimeError): pass
class FrozenViolation(EvaluationError): pass
class CleanupError(EvaluationError): pass


def sha256(path):
    h=hashlib.sha256()
    with Path(path).open('rb') as f:
        for chunk in iter(lambda:f.read(1<<20),b''): h.update(chunk)
    return h.hexdigest()


def digest(value):
    return hashlib.sha256(json.dumps(value,sort_keys=True,separators=(',',':'),allow_nan=False).encode()).hexdigest()


def write_json(path,value):
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
    temporary=path.with_name(path.name+f'.{os.getpid()}.tmp')
    temporary.write_text(json.dumps(value,ensure_ascii=False,indent=2,allow_nan=False)+'\n',encoding='utf-8')
    os.replace(temporary,path)


def ini_values(data):
    result={};section=''
    for raw in data.decode('utf-8-sig',errors='replace').splitlines():
        line=raw.split(';',1)[0].split('#',1)[0].strip()
        if not line: continue
        if line.startswith('[') and line.endswith(']'): section=line[1:-1].strip().casefold();continue
        if '=' not in line: continue
        key,value=line.split('=',1);identity=(section,key.strip().casefold())
        if identity in result: raise EvaluationError(f'Duplicate INI key: {identity}')
        result[identity]=value.strip()
    return result


def seed_value(data):
    value=ini_values(data).get(('settings','seed'))
    if value is None or not re.fullmatch(r'\d+',value): raise EvaluationError('Missing/invalid Settings.Seed')
    value=int(value)
    if not 0<=value<=0xffffffff: raise EvaluationError('Seed must be uint32')
    return value


def seed_bytes(data,seed):
    if type(seed) is not int or not 0<=seed<=0xffffffff: raise EvaluationError('Seed must be uint32')
    seed_value(data)  # reject duplicates/missing instead of relying on native INI precedence
    section=b'';result=[];changed=0
    for line in data.splitlines(keepends=True):
        stripped=line.lstrip(b'\xef\xbb\xbf \t')
        header=re.match(rb'\[([^]]+)\]',stripped)
        if header: section=header.group(1).strip().lower()
        if section==b'settings' and re.match(rb'(?i)^\s*seed\s*=',stripped):
            line=re.sub(rb'(?i)(^\s*seed\s*=\s*)\d+',lambda m:m.group(1)+str(seed).encode(),line,count=1)
            changed+=1
        result.append(line)
    if changed!=1: raise EvaluationError('Seed replacement is not unique')
    value=b''.join(result)
    if seed_value(value)!=seed: raise EvaluationError('Seed replacement verification failed')
    return value


class SeedGuard(AbstractContextManager):
    """Restore exact original bytes; reject concurrent edits rather than overwrite them."""
    def __init__(self,path,seed,evidence):
        self.path=Path(path);self.seed=seed;self.evidence=Path(evidence)
    def __enter__(self):
        self.original=self.path.read_bytes();self.staged=seed_bytes(self.original,self.seed)
        self.evidence.mkdir(parents=True,exist_ok=False)
        (self.evidence/'spawn.ini.original').write_bytes(self.original)
        (self.evidence/'spawn.ini.seeded').write_bytes(self.staged)
        self.path.write_bytes(self.staged)
        if self.path.read_bytes()!=self.staged: raise EvaluationError('Seed write failed')
        return self
    def __exit__(self,*exc):
        if self.path.read_bytes()!=self.staged:
            raise EvaluationError(f'Concurrent spawn.ini change; backup retained: {self.evidence}')
        self.path.write_bytes(self.original)
        if self.path.read_bytes()!=self.original: raise EvaluationError('Config restoration failed')
        write_json(self.evidence/'RESTORED.json',{'restored':True,'original_sha256':hashlib.sha256(self.original).hexdigest()})


def cases(episodes,parallel,seed_base):
    if type(episodes) is not int or episodes<1: raise EvaluationError('episodes must be positive')
    if type(parallel) is not int or not 1<=parallel<=5: raise EvaluationError('parallel must be 1..5')
    if type(seed_base) is not int or seed_base<0 or seed_base+episodes-1>0xffffffff: raise EvaluationError('seed range invalid')
    return [{'case_id':f'case-{i:04d}','index':i,'engine_seed':seed_base+i,'slot':i%parallel} for i in range(episodes)]


def game_dir(root,slot): return Path(root)/('test-game' if slot==0 else f'test-game-{slot}')


def preflight(root,parallel):
    copies=[]
    for slot in range(parallel):
        folder=game_dir(root,slot).resolve()
        spawn=folder/'spawn.ini';values=ini_values(spawn.read_bytes());seed_value(spawn.read_bytes())
        for key,expected in REQUIRED.items():
            if values.get(key,'').casefold()!=expected.casefold(): raise EvaluationError(f'{folder}: protocol mismatch {key}')
        semantic={f'{s}.{k}':v for (s,k),v in values.items() if (s,k)!=('settings','seed')}
        copies.append({'slot':slot,'game_dir':str(folder),'spawn_ini_sha256':sha256(spawn),
                       'spawn_semantic_sha256':digest(semantic),'map_sha256':sha256(folder/'spawnmap.ini'),
                       'game_exe_sha256':sha256(folder/'gamemd-spawn-ra2yrcpp.exe'),
                       'bridge_dll_sha256':sha256(folder/'libra2yrcpp.dll')})
    if len({r['map_sha256'] for r in copies})!=1: raise EvaluationError('Game copies have different maps')
    if len({r['spawn_semantic_sha256'] for r in copies})!=1: raise EvaluationError('Game copies have different settings')
    return copies


def make_plan(root,models,episodes,parallel,seed_base,seconds=300.,mode='deterministic',render_off=True):
    plan_cases=cases(episodes,parallel,seed_base)
    if not math.isfinite(seconds) or seconds<=0: raise EvaluationError('Invalid episode duration')
    if mode not in ('deterministic','stochastic'): raise EvaluationError('Invalid policy mode')
    copies=preflight(root,parallel)
    protocol={**PROTOCOL,'episode_seconds':float(seconds),'policy_mode':mode,'render_off':bool(render_off),
              'map_sha256':copies[0]['map_sha256'],'spawn_semantic_sha256':copies[0]['spawn_semantic_sha256']}
    return {'schema':SCHEMA,'protocol':protocol,'protocol_hash':digest(protocol),
            'case_plan':plan_cases,'case_plan_hash':digest(plan_cases),'parallel':parallel,
            'models':{k:{'path':str(Path(p).resolve()),'sha256':sha256(p)} for k,p in models.items()},
            'copies':copies,'training':False,'used_for_training':False}


def port_free(port):
    with socket.socket(socket.AF_INET,socket.SOCK_STREAM) as sock:
        try: sock.bind(('127.0.0.1',int(port)));return True
        except OSError: return False


def wait_ports(ports,timeout=30.,check=port_free,sleep=time.sleep):
    deadline=time.monotonic()+timeout
    while True:
        if all(check(p) for p in ports): return
        if time.monotonic()>=deadline: raise CleanupError(f'Ports not free: {ports}')
        sleep(min(.25,max(0,deadline-time.monotonic())))


def windows_json(script,payload=None):
    if os.name!='nt': raise EvaluationError('Live process control is Windows-only')
    binary=shutil.which('pwsh.exe') or shutil.which('powershell.exe')
    if not binary: raise EvaluationError('PowerShell not found')
    prefix="$ErrorActionPreference='Stop'; [Console]::OutputEncoding=[Text.UTF8Encoding]::new($false); "
    result=subprocess.run([binary,'-NoProfile','-NonInteractive','-Command',prefix+script],
                          input=json.dumps(payload) if payload is not None else None,
                          capture_output=True,text=True,encoding='utf-8',creationflags=subprocess.CREATE_NO_WINDOW,timeout=30)
    if result.returncode: raise CleanupError(result.stderr[-1500:])
    text=result.stdout.strip().lstrip('\ufeff')
    return json.loads(text) if text else []


def snapshot_processes():
    data=windows_json("@(Get-CimInstance Win32_Process | Where-Object {$_.CreationDate} | ForEach-Object { [pscustomobject]@{pid=[int]$_.ProcessId; parent=[int]$_.ParentProcessId; name=$_.Name; path=$_.ExecutablePath; created=$_.CreationDate.ToString('o')} }) | ConvertTo-Json -Compress")
    return data if isinstance(data,list) else [data]


def normalized_path(path): return os.path.normcase(os.path.abspath(str(path)))


def assert_copies_idle(snapshot,allowed_dirs):
    allowed={normalized_path(Path(folder)/name) for folder in allowed_dirs
             for name in ('gamemd-spawn-ra2yrcpp.exe','gamemd.exe','Syringe.exe')}
    for record in snapshot:
        if record.get('path') and normalized_path(record['path']) in allowed:
            raise CleanupError(f'Game-copy process already exists; no ownership to adopt: {record["pid"]}')
        if not record.get('path') and record.get('name','').casefold() in ('gamemd-spawn-ra2yrcpp.exe','gamemd.exe'):
            raise CleanupError('Cannot identify an existing game process path')


def owned_games(snapshot,worker_pids,allowed_dirs):
    descendants=set(worker_pids);changed=True
    while changed:
        changed=False
        for record in snapshot:
            if record['parent'] in descendants and record['pid'] not in descendants:
                descendants.add(record['pid']);changed=True
    allowed={normalized_path(Path(folder)/name) for folder in allowed_dirs
             for name in ('gamemd-spawn-ra2yrcpp.exe','gamemd.exe','Syringe.exe')}
    return [r for r in snapshot if r['pid'] in descendants and r.get('path') and normalized_path(r['path']) in allowed]


def verified_owned(claims,current,allowed_dirs):
    allowed={normalized_path(Path(folder)/name) for folder in allowed_dirs
             for name in ('gamemd-spawn-ra2yrcpp.exe','gamemd.exe','Syringe.exe')}
    now={r['pid']:r for r in current};targets=[]
    for claim in claims:
        if not claim.get('path') or normalized_path(claim['path']) not in allowed: raise CleanupError('Ownership claim outside game copies')
        record=now.get(claim['pid'])
        if record is None: continue
        if record.get('created')!=claim.get('created') or normalized_path(record.get('path') or '')!=normalized_path(claim['path']):
            raise CleanupError('Process identity changed; refusing to stop PID')
        targets.append(record)
    return targets


def stop_owned(claims,allowed_dirs,snapshot=snapshot_processes,stop=None):
    if not claims:return
    targets=verified_owned(claims,snapshot(),allowed_dirs)
    if not targets: return
    if stop is not None: stop(targets);return
    # Recheck identity in the same PowerShell invocation immediately before Stop-Process.
    windows_json("$targets=([Console]::In.ReadToEnd()|ConvertFrom-Json); foreach($t in $targets) { "
                 "$p=Get-CimInstance Win32_Process -Filter ('ProcessId = '+[int]$t.pid); "
                 "if(!$p){continue}; if($p.ExecutablePath -ine $t.path -or $p.CreationDate.ToString('o') -ne $t.created){throw 'PID identity changed'}; "
                 "Stop-Process -Id ([int]$t.pid) -Force -ErrorAction Stop; "
                 "Wait-Process -Id ([int]$t.pid) -Timeout 10 -ErrorAction SilentlyContinue }; '[]'",targets)


def frozen_file(path,expected):
    if sha256(path)!=expected: raise FrozenViolation(f'Model file hash changed: {path}')


def validate_frozen(value,expected):
    valid=(value.get('model_sha256_before')==expected and value.get('model_sha256_after')==expected
           and value.get('optimizer_updates_before') is not None
           and value.get('optimizer_updates_after')==value.get('optimizer_updates_before')
           and bool(value.get('policy_sha256_before'))
           and value.get('policy_sha256_after')==value.get('policy_sha256_before'))
    if not valid: raise FrozenViolation('Frozen policy assertion failed')
    return True


def validate_case(row,case,seconds):
    for key in ('case_id','engine_seed','slot'):
        if type(row.get(key)) is not type(case[key]) or row.get(key)!=case[key]: raise EvaluationError(f'Result has wrong {key}')
    if row.get('kind') not in ('win','defeat','timeout'): raise EvaluationError('Invalid/infrastructure outcome')
    if row['kind']!='timeout' and not row.get('outcome_confirmed'): raise EvaluationError('Unconfirmed outcome')
    if row['kind']=='timeout' and float(row.get('elapsed_s') or 0)<seconds-1: raise EvaluationError('Premature timeout')
    if bool(row.get('is_win'))!=(row['kind']=='win'): raise EvaluationError('Conflicting victory flag')


def retry_cases(plan_cases,execute,cleanup,guard,seconds,max_retries):
    """Small injectable scheduler: retry a missing case, never replace its identity/seed/slot."""
    pending=list(plan_cases);accepted={};attempts=[]
    def clean():
        if cleanup() is False:raise CleanupError('Cleanup returned failure')
    for attempt in range(max_retries+1):
        if not pending: break
        for start in range(0,len(pending),len({c['slot'] for c in plan_cases})):
            wave=pending[start:start+len({c['slot'] for c in plan_cases})]
            # Retried cases can share a slot; split those into separate waves.
            if len({c['slot'] for c in wave})!=len(wave):
                wave_groups=[[c] for c in wave]
            else: wave_groups=[wave]
            for group in wave_groups:
                clean();guard()
                try: results=execute(group,attempt)
                finally: clean()
                guard()
                by_case={}
                for result in results:
                    identity=result.get('case_id')
                    if identity in by_case or identity not in {c['case_id'] for c in group}: raise EvaluationError('Duplicate/unexpected worker result')
                    by_case[identity]=result
                for case in group:
                    result=by_case.get(case['case_id'],{'case_id':case['case_id'],'ok':False,'error':'missing worker result'})
                    attempts.append({**case,'attempt':attempt,'result':result})
                    if result.get('ok'):
                        validate_case(result['row'],case,seconds)
                        if case['case_id'] in accepted: raise EvaluationError('Duplicate accepted case')
                        accepted[case['case_id']]=result
        pending=[c for c in plan_cases if c['case_id'] not in accepted]
    return accepted,attempts,pending
