"""Process-scoped helpers for hidden, read-only observer collection."""
from pathlib import Path
import json,os,subprocess,threading,time
import psutil
from dsh_episode.render_off import RenderOffController
import winapi

def write_json(path,data):
    path=Path(path);tmp=path.with_name(path.name+f'.{os.getpid()}.tmp')
    tmp.write_text(json.dumps(data,ensure_ascii=False,indent=2,default=str),encoding='utf-8');os.replace(tmp,path)

def copy_processes(game_dir):
    expected=os.path.normcase(str((Path(game_dir)/'gamemd-spawn-ra2yrcpp.exe').resolve()))
    found=[]
    for proc in psutil.process_iter(['pid','name','exe','create_time']):
        try:
            if proc.info['exe'] and os.path.normcase(proc.info['exe'])==expected:
                found.append({'pid':proc.pid,'path':proc.info['exe'],'created':proc.info['create_time']})
        except (psutil.NoSuchProcess,psutil.AccessDenied):pass
    return found

def validate_port(game_dir,port):
    data=json.loads((Path(game_dir)/'ra2yrcpp.json').read_text(encoding='utf-8-sig'))
    return data['port']==port

def launch_hidden(game_dir,run_dir):
    run_dir=Path(run_dir);startup=subprocess.STARTUPINFO();startup.dwFlags|=subprocess.STARTF_USESHOWWINDOW;startup.wShowWindow=0
    with (run_dir/'game-stdout.log').open('wb') as out,(run_dir/'game-stderr.log').open('wb') as err:
        proc=subprocess.Popen([str(Path(game_dir)/'gamemd-spawn-ra2yrcpp.exe'),'-SPAWN','-CD'],cwd=str(game_dir),stdout=out,stderr=err,
                              startupinfo=startup,creationflags=subprocess.CREATE_NEW_PROCESS_GROUP|subprocess.CREATE_NO_WINDOW)
    identity={'pid':proc.pid,'path':str(Path(game_dir)/'gamemd-spawn-ra2yrcpp.exe'),'created':psutil.Process(proc.pid).create_time()}
    write_json(run_dir/'OWNED_GAME.json',identity)
    def hide_loop():
        last=0
        while proc.poll() is None:
            try:
                windows=[w for w in winapi.list_windows() if w['pid']==proc.pid]
                for window in windows:winapi.hide_window(window['hwnd'])
                if time.monotonic()-last>5:
                    write_json(run_dir/'WINDOW_STATUS.json',{'pid':proc.pid,'checked_at':time.time(),'window_count':len(windows),
                              'visible_after_hide':any(winapi.is_window_visible(w['hwnd']) for w in windows)})
                    last=time.monotonic()
            except Exception:pass
            time.sleep(.05)
    threading.Thread(target=hide_loop,name='observer-hide',daemon=True).start()
    return proc

def enable_headless(proc,run_dir):
    (Path(run_dir)/'render-off').mkdir(exist_ok=True)
    controller=RenderOffController(proc.pid,evidence_dir=Path(run_dir)/'render-off',stall_seconds=30)
    controller.enable()
    return controller

def cleanup_owned(proc,controller):
    # The retained Windows process handle cannot refer to a recycled PID.
    if proc.poll() is None:proc.kill()
    proc.wait(timeout=15)
    if controller is not None:controller.process_exited(proc.returncode)
    return {'killed_pid':proc.pid,'remaining_game_processes':0,'cleanup_ok':proc.poll() is not None,'scope':'owned Popen handle only'}
