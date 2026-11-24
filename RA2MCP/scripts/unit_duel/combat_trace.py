"""Observe native combat callbacks in our isolated duel process only.

Frida adds temporary function instrumentation, but no argument/return/game
value is modified. No DLL, bridge contract or protected model is changed.
"""
from pathlib import Path
import gzip,json,sys,threading,hashlib
ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT/'tools/duel-instrumentation'))
import frida

SCRIPT=r'''
const records=[];let seq=0;let depths={};const origins=new Map();const active={};
function i(p,o){return p.add(o).readS32();}
function u(p,o){return p.add(o).readU32();}
function frame(){return ptr('0xA8ED84').readU32();}
function ident(p){try{return p.isNull()?null:p.add(0x24).readUtf8String();}catch(e){return null;}}
function obj(p){
 if(p.isNull())return null;
 try{const v=u(p,0);const o={address:p.toUInt32(),id:u(p,0x10),hp:i(p,0x6c),house:u(p,0x21c),vtable:v,alive:!!p.add(0x90).readU8(),x:i(p,0x9c),y:i(p,0xa0)};
 if(v===0x7eb058){o.type=ident(p.add(0x6c0).readPointer());o.infantry_sequence=i(p,0x6c4);}
 if(v===0x7f5c70)o.type=ident(p.add(0x6c4).readPointer());
 return o;}catch(e){return {address:p.toUInt32(),error:String(e)};}
}
function emit(r){r.event_id=++seq;records.push(r);}
function flush(){if(records.length)send({kind:'batch',records:records.splice(0)});}
Interceptor.attach(ptr('0x4664c0'),{onEnter(args){
 const sp=this.context.esp;const address=this.context.ecx.toUInt32();
 origins.set(address,{projectile:address,created_frame:frame(),attacker:obj(sp.add(12).readPointer()),target:obj(sp.add(8).readPointer()),projectile_type:ident(sp.add(4).readPointer()),damage:sp.add(16).readS32(),warhead_id:ident(sp.add(20).readPointer())});
}});
Interceptor.attach(ptr('0x4690b0'),{onEnter(args){this.key=this.threadId;this.previous=active[this.key];active[this.key]=origins.get(this.context.ecx.toUInt32())||null;},onLeave(){active[this.key]=this.previous;}});
const damage=ptr('0x701900');
if(Process.arch!=='ia32')throw Error('Requires verified 32-bit YR executable');
if(damage.readU32()!==0x00b4ec81)throw Error('ReceiveDamage prologue mismatch');
Interceptor.attach(damage,{
 onEnter(args){
  const sp=this.context.esp;this.victim=this.context.ecx;
  this.damagePtr=sp.add(4).readPointer();
  this.r={kind:'damage',frame:frame(),victim:obj(this.victim),attacker:obj(sp.add(16).readPointer()),damage_input:this.damagePtr.readS32(),distance:sp.add(8).readS32(),warhead:sp.add(12).readU32(),warhead_id:ident(sp.add(12).readPointer()),ignore_defenses:!!sp.add(20).readU32(),source_house:sp.add(28).readU32()};
  this.r.engine_attacker=this.r.attacker;this.r.attacker_source='native_receive_damage';
  this.r.projectile_origin=active[this.threadId]||null;
  if(!this.r.attacker&&this.r.projectile_origin){this.r.attacker=this.r.projectile_origin.attacker;this.r.attacker_source='tracked_projectile_origin';}
 },
 onLeave(ret){
  this.r.victim_after= obj(this.victim);this.r.damage_state=ret.toInt32();
  try{this.r.damage_adjusted=this.damagePtr.readS32();}catch(e){}
  const a=this.r.victim,b=this.r.victim_after;
  if(a&&b&&a.hp!==undefined&&b.hp!==undefined)this.r.hp_loss=Math.max(0,a.hp)-Math.max(0,b.hp);
  emit(this.r);
 }
});
for(const [slot,address] of [['0x7f603c','0x741340'],['0x7eb424','0x51df60']]){
 if(!ptr(slot).readPointer().equals(ptr(address)))throw Error('Fire vtable mismatch');
 Interceptor.attach(ptr(address),{onEnter(args){const sp=this.context.esp;this.r={kind:'fire',frame:frame(),attacker:obj(this.context.ecx),target:obj(sp.add(4).readPointer()),weapon_index:sp.add(8).readS32()};},onLeave(ret){this.r.projectile=ret.toUInt32();emit(this.r);}});
}
// UnitClass override calls the Techno base. Depth suppresses duplicates.
for(const address of ['0x744720','0x702d40'])Interceptor.attach(ptr(address),{
 onEnter(args){let key=this.threadId;this.key=key;let d=depths[key]||0;depths[key]=d+1;this.top=d===0;
  if(this.top){const native=obj(this.context.esp.add(4).readPointer());const origin=active[this.threadId]||null;
   emit({kind:'kill_credit',frame:frame(),victim:obj(this.context.ecx),engine_credited_killer:native,killer:native||(origin?origin.attacker:null),killer_source:native?'native_register_destruction':origin?'tracked_projectile_origin':'UNKNOWN',projectile_origin:origin,function:address});}
 },onLeave(){depths[this.key]--;}
});
setInterval(flush,100);
rpc.exports={flush(){flush();return {events:seq,frame:frame()};}};
send({kind:'ready',frame:frame(),arch:Process.arch,damage_function:damage.toString(),kill_functions:['0x744720','0x702d40']});
'''

class CombatTrace:
    def __init__(self,proc,out):
        import psutil
        p=psutil.Process(proc.pid)
        if Path(p.exe()).resolve()!=ROOT/'test-game-duel/gamemd-spawn-ra2yrcpp.exe':raise RuntimeError('Not our duel executable')
        digest=hashlib.sha256(Path(p.exe()).read_bytes()).hexdigest()
        if digest!='f72cbdf1a6eb292f5e702a496128904b6a098b71382f31dc97281487e5f7bee0':raise RuntimeError('Duel executable changed; instrumentation must be revalidated')
        self.out=out;self.file=gzip.open(out/'COMBAT_EVENTS.jsonl.gz','wt',encoding='utf-8');self.ready=threading.Event();self.rows=0;self.errors=[];self.meta={}
        self.session=frida.attach(proc.pid);self.script=self.session.create_script(SCRIPT)
        def receive(message,data):
            if message['type']=='send':
                payload=message['payload']
                if payload['kind']=='ready':self.meta=payload;self.ready.set()
                if payload['kind']=='batch':
                    for row in payload['records']:self.file.write(json.dumps(row,ensure_ascii=False)+'\n');self.rows+=1
                    self.file.flush()
            else:self.errors.append(message)
        self.script.on('message',receive);self.script.load()
        if not self.ready.wait(8):self.close();raise RuntimeError('Combat instrumentation did not become ready: '+str(self.errors))
        (out/'COMBAT_HOOK_READY.json').write_text(json.dumps(self.meta,indent=2),encoding='utf-8')
        (out/'combat_trace.js').write_text(SCRIPT,encoding='utf-8')
    def close(self):
        try:self.meta['flush']=self.script.exports_sync.flush()
        except Exception as exc:self.errors.append(str(exc))
        try:self.script.unload();self.session.detach()
        except Exception as exc:self.errors.append(str(exc))
        self.file.close()
        (self.out/'COMBAT_HOOK_STATUS.json').write_text(json.dumps(dict(**self.meta,records=self.rows,errors=self.errors),ensure_ascii=False,indent=2),encoding='utf-8')
