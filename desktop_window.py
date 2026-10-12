"""Dedicated Windows app window using installed Edge/Chrome, stdlib only."""
import os,subprocess,webbrowser
from pathlib import Path

def browser_path():
    roots=[os.environ.get(k) for k in ('ProgramFiles','ProgramFiles(x86)','LOCALAPPDATA')]
    for relative in ('Microsoft/Edge/Application/msedge.exe','Google/Chrome/Application/chrome.exe'):
        for root in roots:
            if root:
                path=Path(root)/relative
                if path.is_file(): return path
    return None

def open_window(url,data):
    executable=browser_path() if os.name=='nt' else None
    if executable:
        profile=Path(data)/'desktop_profile'
        subprocess.Popen([str(executable),'--app='+url,'--user-data-dir='+str(profile),'--no-first-run','--no-default-browser-check','--window-size=1400,950'])
        return True
    print('Edge/Chrome non trovato: apertura nel browser predefinito.')
    webbrowser.open(url);return False
