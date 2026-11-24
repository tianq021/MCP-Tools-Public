"""Native message layout for the requested 27-pixel bitmap font.

Only MessageList UI height/Init argument is changed in the isolated duel
process. Combat hooks, gameplay arguments and engine binaries are unchanged.
"""
from pathlib import Path
import json,time
SCRIPT=r'''
const list=ptr('0xa8bc60');const wanted=29;
function rows(){try{let p=ptr('0x89c4d0').readPointer();return p.add(4).readPointer().add(8).readU32();}catch(e){return 0;}}
function adjust(p){if(p.equals(list)&&rows()===27)p.add(20).writeS32(wanted);}
function snapshot(reason){
 let labels=[];try{let p=list.readPointer();for(let n=0;!p.isNull()&&n<14;n++){
 labels.push({y:p.add(16).readS32(),text:p.add(48).readPointer().readUtf16String()});p=p.add(4).readPointer();}}catch(e){}
 send({frame:ptr('0xa8ed84').readU32(),game_speed:ptr('0xa8eb60').readS32(),native_fps:ptr('0xabcd44').readU32(),font_rows:rows(),line_height:list.add(20).readS32(),labels:labels,reason:reason});
}
// MessageListClass::Init(X,Y,MaxMsg,MaxChars,Height,...), x86 thiscall.
Interceptor.attach(ptr('0x5d3a60'),{onEnter(){this.ui=this.context.ecx;if(this.ui.equals(list)&&rows()===27)this.context.esp.add(20).writeS32(wanted);},onLeave(){adjust(this.ui);}});
// Init may already have run before the observer attaches. Normalize before
// each native insertion/reflow so a subsequent Init cannot restore 19px.
for(const address of ['0x5d3ba0','0x5d4bf0'])Interceptor.attach(ptr(address),{onEnter(){adjust(this.context.ecx);},onLeave(){}});
const timer=setInterval(()=>snapshot('sample'),250);
rpc.exports={finish(){clearInterval(timer);snapshot('final');return true;}};
snapshot('attached');
'''

class NativeDisplay:
    def __init__(self,session,out):
        self.out=Path(out);self.samples=[];self.errors=[];self.script=session.create_script(SCRIPT)
        def receive(message,data):
            if message['type']=='send':
                row=message['payload'];row['observed_monotonic']=time.monotonic();self.samples.append(row)
            else:self.errors.append(message)
        self.script.on('message',receive);self.script.load()
        (self.out/'display_style.js').write_text(SCRIPT,encoding='utf-8')
    def close(self):
        try:self.script.exports_sync.finish();self.script.unload()
        except Exception as exc:self.errors.append(repr(exc))
        (self.out/'NATIVE_DISPLAY.json').write_text(json.dumps(dict(samples=self.samples,errors=self.errors),ensure_ascii=False,indent=2),encoding='utf-8')
