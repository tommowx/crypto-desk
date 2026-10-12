"""Stable Windows entrypoint. Installed by the single-file setup."""
import ctypes,json,os,subprocess,sys,time,urllib.request
from pathlib import Path
BASE=Path(__file__).resolve().parent
DATA=BASE/'data'
URL='http://127.0.0.1:8877'
def state():
    with urllib.request.urlopen(URL+'/api/state',timeout=1) as r:
        s=json.load(r)
    if not all(k in s for k in ('version','token','portfolio','deep')):
        raise ValueError('La porta 8877 e utilizzata da un altro programma.')
    return s

def main():
    DATA.mkdir(exist_ok=True)
    try:
        state()
        # The stable bootstrap keeps only launcher.pyw and updater.py at BASE.
        # UI code lives inside the currently selected version directory.
        from updater import _current
        _,running_root,_=_current(BASE)
        sys.path.insert(0,str(running_root))
        from desktop_window import open_window
        open_window(URL,DATA)
        return
    except (OSError,urllib.error.URLError):
        pass
    from updater import check_and_update
    release,message,changed=check_and_update(BASE)
    root=(BASE/'versions'/release).resolve()
    if root.parent!=(BASE/'versions').resolve() or not (root/'app.py').is_file():
        raise RuntimeError('Installazione incompleta. Apri nuovamente il file di aggiornamento.')
    sys.path.insert(0,str(root))
    from desktop_window import open_window
    try:
        state()
    except (OSError,urllib.error.URLError):
        pid_file=BASE/'running.pid'
        if os.name=='nt' and pid_file.exists():
            from ctypes import wintypes
            kernel=ctypes.WinDLL('kernel32',use_last_error=True)
            kernel.OpenProcess.argtypes=[wintypes.DWORD,wintypes.BOOL,wintypes.DWORD]
            kernel.OpenProcess.restype=wintypes.HANDLE
            kernel.WaitForSingleObject.argtypes=[wintypes.HANDLE,wintypes.DWORD]
            kernel.CloseHandle.argtypes=[wintypes.HANDLE]
            handle=kernel.OpenProcess(0x00100000,False,int(pid_file.read_text()))
            if handle:
                try: alive=kernel.WaitForSingleObject(handle,0)==258
                finally: kernel.CloseHandle(handle)
                if alive: raise RuntimeError('Crypto Desk sta ancora avviando o salvando. Riprova fra qualche secondo.')
        env=dict(os.environ,CRYPTO_DESK_DATA=str(DATA))
        def launch_and_check(version_root):
            with (DATA/'startup.log').open('ab') as log:
                proc=subprocess.Popen([sys.executable,str(version_root/'app.py'),'--no-browser'],cwd=version_root,env=env,stdout=log,stderr=log)
            (BASE/'running.pid').write_text(str(proc.pid))
            for _ in range(80):
                if proc.poll() is not None:return False,proc
                try:
                    running=state()
                    if str(running.get('version'))==(version_root/'version.txt').read_text(encoding='ascii').strip():return True,proc
                    return False,proc
                except (OSError,urllib.error.URLError):time.sleep(.25)
            return False,proc
        healthy,child=launch_and_check(root)
        if not healthy and changed:
            if child.poll() is None:child.terminate()
            from updater import rollback
            if rollback(BASE):
                fallback=(BASE/'versions'/(BASE/'current.txt').read_text().strip()).resolve()
                healthy,_=launch_and_check(fallback)
        if not healthy:raise RuntimeError('Il motore non si avvia. Crypto Desk ha conservato la versione precedente. Dettagli in '+str(DATA/'startup.log'))
    open_window(URL,DATA)

if __name__=='__main__':
    try:main()
    except Exception as e:ctypes.windll.user32.MessageBoxW(0,str(e),'Crypto Desk',16)
