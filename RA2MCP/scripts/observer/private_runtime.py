"""Launch observer games on a non-input Windows desktop, with no desktop switch."""
from pathlib import Path
import ctypes,ctypes.wintypes as w,json,os,subprocess,threading,time,uuid
import psutil
from observer.batch_runtime import write_json,copy_processes,validate_port,enable_headless
from observer import batch_runtime as legacy
import winapi

U=ctypes.WinDLL('user32',use_last_error=True);K=ctypes.WinDLL('kernel32',use_last_error=True)
U.CreateDesktopW.argtypes=[w.LPCWSTR,w.LPCWSTR,w.LPVOID,w.DWORD,w.DWORD,w.LPVOID];U.CreateDesktopW.restype=w.HANDLE
U.CloseDesktop.argtypes=[w.HANDLE];U.CloseDesktop.restype=w.BOOL
U.SetThreadDesktop.argtypes=[w.HANDLE];U.SetThreadDesktop.restype=w.BOOL
U.GetThreadDesktop.argtypes=[w.DWORD];U.GetThreadDesktop.restype=w.HANDLE
K.GetCurrentThreadId.restype=w.DWORD
U.OpenInputDesktop.argtypes=[w.DWORD,w.BOOL,w.DWORD];U.OpenInputDesktop.restype=w.HANDLE
U.GetUserObjectInformationW.argtypes=[w.HANDLE,ctypes.c_int,w.LPVOID,w.DWORD,ctypes.POINTER(w.DWORD)];U.GetUserObjectInformationW.restype=w.BOOL

def input_desktop_name():
    desk=U.OpenInputDesktop(0,False,1)
    if not desk:return 'UNAVAILABLE'
    try:
        buf=ctypes.create_unicode_buffer(512);needed=w.DWORD()
        return buf.value if U.GetUserObjectInformationW(desk,2,buf,ctypes.sizeof(buf),ctypes.byref(needed)) else 'UNAVAILABLE'
    finally:U.CloseDesktop(desk)

def launch_hidden(game_dir,run_dir):
    run_dir=Path(run_dir);before=input_desktop_name();original=U.GetThreadDesktop(K.GetCurrentThreadId())
    name='RA2Collect_'+uuid.uuid4().hex[:20]
    desktop=U.CreateDesktopW(name,None,None,0,0x1ff,None)
    if not desktop:raise ctypes.WinError(ctypes.get_last_error())
    if U.GetThreadDesktop(K.GetCurrentThreadId())!=original:
        if not U.SetThreadDesktop(original):
            U.CloseDesktop(desktop);raise ctypes.WinError(ctypes.get_last_error())
    startup=subprocess.STARTUPINFO();startup.lpDesktop='winsta0\\'+name
    startup.dwFlags|=subprocess.STARTF_USESHOWWINDOW;startup.wShowWindow=0
    # This worker owns no other tasks. Children inherit suppressed native crash dialogs.
    K.SetErrorMode(0x8003)
    try:
        with (run_dir/'game-stdout.log').open('wb') as out,(run_dir/'game-stderr.log').open('wb') as err:
            proc=subprocess.Popen([str(Path(game_dir)/'gamemd-spawn-ra2yrcpp.exe'),'-SPAWN','-CD'],cwd=str(game_dir),stdout=out,stderr=err,
                                  startupinfo=startup,creationflags=subprocess.CREATE_NEW_PROCESS_GROUP|subprocess.CREATE_NO_WINDOW|subprocess.BELOW_NORMAL_PRIORITY_CLASS)
    except BaseException:
        U.CloseDesktop(desktop);raise
    try:
        process=psutil.Process(proc.pid);process.nice(psutil.BELOW_NORMAL_PRIORITY_CLASS)
        allowed=psutil.Process().cpu_affinity();process.cpu_affinity(allowed[-min(8,len(allowed)):])
        proc._observer_desktop=desktop
        identity={'pid':proc.pid,'path':str(Path(game_dir)/'gamemd-spawn-ra2yrcpp.exe'),'created':process.create_time(),
                  'desktop':name,'startup_desktop':startup.lpDesktop,'input_desktop_before':before,'priority':'BELOW_NORMAL','cpu_affinity':process.cpu_affinity()}
        write_json(run_dir/'OWNED_GAME.json',identity)
        stop=threading.Event();proc._observer_hide_stop=stop
        def keep_private():
            previous=U.GetThreadDesktop(K.GetCurrentThreadId());seen=False;last=0
            try:
                if not U.SetThreadDesktop(desktop):raise ctypes.WinError(ctypes.get_last_error())
                while proc.poll() is None and not stop.is_set():
                    windows=[v for v in winapi.list_windows() if v['pid']==proc.pid];seen=seen or bool(windows)
                    for window in windows:winapi.hide_window(window['hwnd'])
                    if time.monotonic()-last>3:
                        process.nice(psutil.BELOW_NORMAL_PRIORITY_CLASS)
                        write_json(run_dir/'WINDOW_STATUS.json',{'pid':proc.pid,'checked_at':time.time(),'window_count':len(windows),
                                   'visible_after_hide':any(winapi.is_window_visible(v['hwnd']) for v in windows),
                                   'desktop':name,'private_window_seen':seen,'input_desktop':input_desktop_name(),
                                   'not_input_desktop':input_desktop_name()!=name,'isolation':'STARTUPINFO.lpDesktop, no desktop switch'})
                        last=time.monotonic()
                    stop.wait(.1)
            except (psutil.NoSuchProcess,ProcessLookupError):pass
            except BaseException as exc:write_json(run_dir/'WINDOW_ERROR.json',{'error':type(exc).__name__+':'+str(exc)})
            finally:U.SetThreadDesktop(previous)
        thread=threading.Thread(target=keep_private,name='observer-private-window',daemon=True);proc._observer_hide_thread=thread;thread.start()
        return proc
    except BaseException:
        if proc.poll() is None:proc.kill()
        proc.wait(timeout=15);U.CloseDesktop(desktop);raise

def cleanup_owned(proc,controller):
    result=legacy.cleanup_owned(proc,controller)
    if hasattr(proc,'_observer_hide_stop'):proc._observer_hide_stop.set()
    if hasattr(proc,'_observer_hide_thread'):proc._observer_hide_thread.join(timeout=3)
    if getattr(proc,'_observer_desktop',None):
        result['private_desktop_handle_closed']=bool(U.CloseDesktop(proc._observer_desktop));proc._observer_desktop=None
    return result

def notify_once(plan,out,summary):
    """One completion notice, following the user's earlier Feishu instruction."""
    out=Path(out)
    claim=out/'FEISHU_ATTEMPT.json'
    try:
        with claim.open('x',encoding='utf-8') as f:json.dump({'at':time.time(),'retry':False},f)
    except FileExistsError:return
    message=(f"后台采集第二批已收尾\n完整通过：{summary['completed_games']} 局；部分/失败：{summary['partial_or_failed_games']} 局。\n"
             f"完整局全局样本：{summary.get('accepted_global_state_samples',0):,}；每局七个电脑。\n"
             "采集在独立后台桌面运行，无游戏窗口切入主桌面；本批仅采集，未训练。\n"
             f"结果目录：{out}")
    path=out/'FEISHU_MESSAGE.txt';path.write_text(message,encoding='utf-8')
    try:
        result=subprocess.run(['F:/conda/envs/python_mmx_LLM/python.exe','-X','utf8','-B','C:/Users/hp/.codex/skills/feishu-notify/scripts/send_message.py',
                               '--config','G:/Codex_mmx/feishu-notify/config.local.json','--file',str(path)],capture_output=True,encoding='utf-8',timeout=30,creationflags=subprocess.CREATE_NO_WINDOW)
        data={'returncode':result.returncode,'stdout':result.stdout,'stderr':result.stderr,'confirmed':result.returncode==0}
    except BaseException as exc:data={'confirmed':False,'error':type(exc).__name__+':'+str(exc),'retry':False}
    write_json(out/'FEISHU_DELIVERY.json',data)
