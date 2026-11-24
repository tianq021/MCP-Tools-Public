"""Current character MCP: correlated game-thread snapshots plus separately labelled disk data."""
import base64
import datetime
import json
import os
from pathlib import Path
import re
import time
import uuid

DIAGNOSTICS = Path(r'F:\SteamLibrary\steamapps\common\Borderlands 2\Binaries\Win32\Mods\SessionInspector\diagnostics')


def safe(value):
    if isinstance(value, bytes):
        try:
            text=value.decode('utf-8')
            if all(c.isprintable() or c in '\r\n\t' for c in text): return text
        except UnicodeDecodeError:
            pass
        return {'encoding':'base64','data':base64.b64encode(value).decode()}
    if isinstance(value, dict): return {str(k):safe(v) for k,v in value.items()}
    if isinstance(value, (list,tuple)): return [safe(v) for v in value]
    return value


def load_snapshot(folder):
    path=folder/'latest.json'
    if not path.is_file(): return None
    if path.stat().st_size>8*1024*1024: raise ValueError('快照超过8MB')
    data=json.loads(path.read_text(encoding='utf-8'))
    if not isinstance(data,dict) or data.get('schema')!=1: raise ValueError('快照格式不支持')
    return data


def snapshot(refresh=True, timeout=3.0, folder=DIAGNOSTICS):
    token=None; error=None
    if refresh:
        if not (folder.parent/'__init__.py').is_file():
            error='诊断Mod尚未安装；下次通过启动器启动时安装。'
        else:
            try:
                folder.mkdir(exist_ok=True)
                token=uuid.uuid4().hex
                temporary=folder/('request-'+token+'.tmp')
                try:
                    temporary.write_text(json.dumps({'id':token,'operation':'snapshot','time':time.time()}),encoding='utf-8')
                    os.replace(temporary,folder/'request.json')
                finally:
                    temporary.unlink(missing_ok=True)
                deadline=time.monotonic()+timeout
                while time.monotonic()<deadline:
                    data=load_snapshot(folder)
                    if data and data.get('request_id')==token:
                        return {'fresh_response':True,'request_id':token,'data':data,'error':None}
                    time.sleep(0.1)
                error='未收到本次请求响应：游戏未运行、未推进帧、或Mod版本低于0.3。旧快照不算实时。'
            except Exception as exc:
                error=str(exc)
    try: data=load_snapshot(folder)
    except Exception as exc: data=None;error=str(exc)
    return {'fresh_response':False,'request_id':token,'data':data,'error':error}


def current_character(refresh=True, save_slot='', include_raw=False):
    from bl2toolkit.paths import find_save_root, resolve_save
    from bl2toolkit.saves import SaveFile
    from bl2toolkit.profile import ProfileFile
    from bl2toolkit.parts import decode_resolved
    result={'read_only_gameplay':True,'writes':'Only local snapshot request; never sav/profile/gameplay values.',
            'runtime':snapshot(refresh),'disk':None,'account':None}
    live=result['runtime'].get('data') or {}
    result['runtime']['observed_at']=live.get('utc')
    result['runtime']['current_character_confirmed']=bool(result['runtime']['fresh_response'] and live.get('in_world'))
    if live:
        # Avoid flooding MCP with unrelated world-wide mission definitions.
        live=dict(live);missions=dict(live.get('missions',{}))
        rows=missions.get('missions',[])
        missions['missions']=[m for m in rows if m.get('tracked') or m.get('progress_available')]
        missions['omitted_definition_count']=len(rows)-len(missions['missions'])
        live['missions']=missions
        if not include_raw:live.pop('xp_events_retained',None)
        result['runtime']['data']=live
    try:
        root=find_save_root()
        paths=list(root.glob('*/Save*.sav')) if root else []
        selected=None;match='none'
        if save_slot:
            if not re.fullmatch(r'(?:[0-9]{1,4}|Save[0-9A-Fa-f]{4})(?:\.sav)?',save_slot):
                raise ValueError('只接受角色槽位，不接受任意文件路径')
            selected=resolve_save(save_slot);match='explicit_disk_selection_not_live_identity'
        else:
            identity=live.get('character',{}).get('identity',{})
            sid=identity.get('save_game_id')
            if result['runtime']['current_character_confirmed'] and sid is not None:
                candidates=[p for p in paths if SaveFile(p).summary().get('save_game_id')==sid]
                if len(candidates)==1:selected=candidates[0];match='fresh_runtime_save_id'
                elif len(candidates)>1:match='ambiguous_account_identity'
            if selected is None and paths and match!='ambiguous_account_identity':
                selected=max(paths,key=lambda p:p.stat().st_mtime);match='most_recent_disk_candidate_not_confirmed_current'
        if selected:
            sf=SaveFile(selected)
            inventory=[]
            for section in ('weapons','items','bank'):
                for i,entry in enumerate(sf._iter_item_entries(section)):
                    try:decoded=decode_resolved(entry.code)
                    except Exception as exc:decoded={'error':str(exc)}
                    inventory.append({'section':section,'index':i,'level':entry.level,'decoded':decoded})
            result['disk']={'matching':match,'modified_at':datetime.datetime.fromtimestamp(selected.stat().st_mtime,datetime.timezone.utc).isoformat(),
                'summary':sf.summary(),'op_unlocked':sf.op_level(),'skills':sf.list_skills(),
                'equipment':inventory,'resources':sf.structured.get('resources',[]),
                'missions':sf.structured.get('missions',[]),'not_live':True}
            if include_raw:result['disk']['structured_save']=sf.structured;result['disk']['raw_protobuf']=sf.player
            p=selected.parent/'profile.bin'
            if p.is_file():
                pf=ProfileFile(p)
                result['account']={'source':'Disk profile of selected save account; shared, not live.',
                    'modified_at':datetime.datetime.fromtimestamp(p.stat().st_mtime,datetime.timezone.utc).isoformat(),
                    'golden_keys':pf.golden_keys(),'badass':pf.badass()}
                if include_raw:result['account']['entries']=[vars(e) for e in pf.entries]
        else:result['disk']={'matching':match,'error':'无法确认对应存档'}
    except Exception as exc:result['disk']={'error':str(exc)}
    result['limits']=['Unknown fields remain null, never assumed zero.',
        'No live DPS evaluation; cached-save XP/currency may lag.',
        'Disk currency and account keys are not automatically current in-game values.']
    return safe(result)


def register(mcp):
    @mcp.tool()
    def bl2_current_character(refresh: bool = True, save_slot: str = '', include_raw: bool = False) -> dict:
        """读取当前人物血量护盾、地图任务技能装备；经验货币金钥匙补充磁盘数据并标来源。
        refresh向游戏主线程请求新快照，3秒无响应明确返回旧数据。include_raw保留全部原始字段。
        不修改人物/存档；不得把most_recent_disk_candidate当已确认当前角色。
        """
        return current_character(refresh,save_slot,include_raw)
