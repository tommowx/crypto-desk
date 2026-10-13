"""Dedicated Windows app window using installed Edge/Chrome, stdlib only."""
import ctypes,os,subprocess,webbrowser
from ctypes import wintypes
from pathlib import Path

_WINDOW_PROCESS=None

def browser_path():
    roots=[os.environ.get(k) for k in ('ProgramFiles','ProgramFiles(x86)','LOCALAPPDATA')]
    for relative in ('Microsoft/Edge/Application/msedge.exe','Google/Chrome/Application/chrome.exe'):
        for root in roots:
            if root:
                path=Path(root)/relative
                if path.is_file(): return path
    return None

def open_window(url,data):
    global _WINDOW_PROCESS
    executable=browser_path() if os.name=='nt' else None
    if executable:
        profile=Path(data)/'desktop_profile'
        _WINDOW_PROCESS=subprocess.Popen([str(executable),'--app='+url,'--user-data-dir='+str(profile),'--no-first-run','--no-default-browser-check','--window-size=1400,950'])
        try:
            kernel=ctypes.WinDLL('kernel32',use_last_error=True);kernel.OpenProcess.argtypes=[wintypes.DWORD,wintypes.BOOL,wintypes.DWORD];kernel.OpenProcess.restype=wintypes.HANDLE;kernel.GetProcessTimes.argtypes=[wintypes.HANDLE,ctypes.POINTER(wintypes.FILETIME),ctypes.POINTER(wintypes.FILETIME),ctypes.POINTER(wintypes.FILETIME),ctypes.POINTER(wintypes.FILETIME)];kernel.CloseHandle.argtypes=[wintypes.HANDLE]
            handle=kernel.OpenProcess(0x1000,False,_WINDOW_PROCESS.pid)
            if handle:
                creation=wintypes.FILETIME();exit_time=wintypes.FILETIME();kernel_time=wintypes.FILETIME();user=wintypes.FILETIME()
                if kernel.GetProcessTimes(handle,ctypes.byref(creation),ctypes.byref(exit_time),ctypes.byref(kernel_time),ctypes.byref(user)):
                    stamp=(creation.dwHighDateTime<<32)|creation.dwLowDateTime
                    (Path(data)/'desktop_window.pid').write_text(f'{_WINDOW_PROCESS.pid}\n{stamp}\n{executable.resolve()}',encoding='utf-8')
                kernel.CloseHandle(handle)
        except (OSError,AttributeError):pass
        return True
    print('Edge/Chrome non trovato: apertura nel browser predefinito.')
    webbrowser.open(url);return False


def close_window(data):
    """Close only the app window launched for this installation."""
    process=_WINDOW_PROCESS
    try:
        if os.name=='nt':
            record=Path(data)/'desktop_window.pid'
            if not record.is_file():return False
            fields=record.read_text(encoding='utf-8').splitlines()
            if len(fields)!=3 or not fields[0].isdigit() or not fields[1].isdigit():return False
            pid,expected=int(fields[0]),int(fields[1]);kernel=ctypes.WinDLL('kernel32',use_last_error=True);kernel.OpenProcess.argtypes=[wintypes.DWORD,wintypes.BOOL,wintypes.DWORD];kernel.OpenProcess.restype=wintypes.HANDLE;kernel.GetProcessTimes.argtypes=[wintypes.HANDLE,ctypes.POINTER(wintypes.FILETIME),ctypes.POINTER(wintypes.FILETIME),ctypes.POINTER(wintypes.FILETIME),ctypes.POINTER(wintypes.FILETIME)];kernel.CloseHandle.argtypes=[wintypes.HANDLE];handle=kernel.OpenProcess(0x1000,False,pid)
            if not handle:return False
            try:
                creation=wintypes.FILETIME();exit_time=wintypes.FILETIME();kernel_time=wintypes.FILETIME();user=wintypes.FILETIME()
                if not kernel.GetProcessTimes(handle,ctypes.byref(creation),ctypes.byref(exit_time),ctypes.byref(kernel_time),ctypes.byref(user)):return False
                actual=(creation.dwHighDateTime<<32)|creation.dwLowDateTime
                if actual!=expected:return False
            finally:kernel.CloseHandle(handle)
            flags=getattr(subprocess,'CREATE_NO_WINDOW',0)
            result=subprocess.run(['taskkill','/PID',str(pid),'/T','/F'],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,timeout=5,creationflags=flags)
            try:record.unlink()
            except OSError:pass
            return result.returncode==0
        if process is not None and process.poll() is None:process.terminate();return True
    except (OSError,subprocess.TimeoutExpired,AttributeError):return False
    return False
