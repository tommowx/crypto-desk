"""Small fail-safe updater: HTTPS manifest, SHA-256, staged validation and atomic switch."""
import hashlib,io,json,os,re,shutil,ssl,tempfile,time,urllib.error,urllib.request,uuid,zipfile
from pathlib import Path

MANIFEST='https://raw.githubusercontent.com/tommowx/crypto-desk/main/release.json'
PACKAGE_PREFIX='https://raw.githubusercontent.com/tommowx/crypto-desk/main/releases/'
MAX_ARCHIVE=25*1024*1024
MAX_UNPACKED=20*1024*1024
REQUIRED={'app.py','index.html','engine.py','batch_engine.py','batch_jobs.py','deep_engine.py','deep_jobs.py','history_data.py','desktop_window.py','launcher.pyw','updater.py','app.ico','version.txt','LEGGIMI.txt'}

def _get(url,limit,timeout=20):
    request=urllib.request.Request(url,headers={'User-Agent':'CryptoDesk-Updater/1'})
    with urllib.request.urlopen(request,timeout=timeout,context=ssl.create_default_context()) as response:
        if response.status!=200: raise ValueError('Download non riuscito')
        data=response.read(limit+1)
    if len(data)>limit: raise ValueError('File remoto oltre il limite consentito')
    return data

def _version(v):
    if not isinstance(v,str) or not re.fullmatch(r'\d{1,3}(?:\.\d{1,3}){1,2}',v):
        raise ValueError('Versione remota non valida')
    return tuple(int(n) for n in v.split('.'))

def _current(base):
    name=(base/'current.txt').read_text(encoding='utf-8-sig').strip()
    root=(base/'versions'/name).resolve()
    if root.parent!=(base/'versions').resolve() or not root.is_dir():
        raise ValueError('Versione installata non trovata')
    try: version=(root/'version.txt').read_text(encoding='ascii').strip()
    except OSError: version=name.split('-',1)[0]
    _version(version)
    return name,root,version

def _manifest(raw):
    m=json.loads(raw.decode('utf-8'))
    if not isinstance(m,dict) or m.get('schema')!=1: raise ValueError('Manifest non supportato')
    version=m.get('version');_version(version)
    filename=m.get('file')
    if not isinstance(filename,str) or not re.fullmatch(r'crypto-desk-[0-9.]+\.zip',filename): raise ValueError('Nome pacchetto non valido')
    if filename!=f'crypto-desk-{version}.zip': raise ValueError('Versione e pacchetto non coincidono')
    if m.get('url')!=PACKAGE_PREFIX+filename: raise ValueError('Indirizzo pacchetto non autorizzato')
    digest=m.get('sha256')
    if not isinstance(digest,str) or not re.fullmatch(r'[0-9a-f]{64}',digest): raise ValueError('Hash pacchetto non valido')
    return m

def _install_archive(base,archive,version,digest):
    versions=base/'versions';versions.mkdir(parents=True,exist_ok=True)
    release=f'{version}-{digest[:12]}';target=versions/release
    if target.exists(): return release
    stage=versions/('.stage-'+uuid.uuid4().hex);stage.mkdir()
    try:
        with zipfile.ZipFile(io.BytesIO(archive)) as z:
            infos=z.infolist()
            names=[i.filename for i in infos]
            if len(names)>40 or len(names)!=len(set(names)): raise ValueError('Archivio con nomi duplicati o troppi file')
            if set(names)!=REQUIRED: raise ValueError('Elenco file del pacchetto non autorizzato o incompleto')
            total=0
            for i in infos:
                name=i.filename
                mode=(i.external_attr>>16)&0xffff
                if not name or '/' in name or '\\' in name or name in ('.','..') or i.is_dir(): raise ValueError('Percorso ZIP non consentito')
                if mode and (mode&0o170000)==0o120000: raise ValueError('Link simbolico ZIP non consentito')
                total+=i.file_size
                if i.file_size>8*1024*1024 or total>MAX_UNPACKED: raise ValueError('Archivio espanso oltre il limite')
            z.extractall(stage)
        actual=(stage/'version.txt').read_text(encoding='ascii').strip()
        if actual!=version: raise ValueError('Versione del pacchetto non coincide')
        for py in stage.glob('*.py'):
            compile(py.read_bytes(),str(py),'exec')
        (stage/'__pycache__').exists() and shutil.rmtree(stage/'__pycache__')
        stage.rename(target)
        return release
    except Exception:
        shutil.rmtree(stage,ignore_errors=True)
        raise

def _check_and_update_locked(base):
    """Return (release folder name, message, changed). Network/validation failure leaves current untouched."""
    base=Path(base).resolve();name,root,installed=_current(base)
    try:
        m=_manifest(_get(MANIFEST,32*1024))
        if _version(m['version'])<=_version(installed): return name,'Crypto Desk è aggiornato',False
        payload=_get(m['url'],MAX_ARCHIVE)
        if not hashlib.sha256(payload).hexdigest()==m['sha256']: raise ValueError('Controllo integrità non riuscito; aggiornamento rifiutato')
        release=_install_archive(base,payload,m['version'],m['sha256'])
        new_root=base/'versions'/release
        # New code is staged before replacing the stable launcher/updater; retain backups for rollback.
        replacements=[]
        for filename in ('launcher.pyw','updater.py'):
            src=new_root/filename;dest=base/filename;tmp=base/(filename+'.new')
            shutil.copyfile(src,tmp);replacements.append((dest,tmp))
        old_files={dest:dest.read_bytes() if dest.exists() else None for dest,_ in replacements}
        old_pointer=(base/'current.txt').read_bytes()
        try:
            for dest,tmp in replacements:os.replace(tmp,dest)
            p=base/'current.txt';prev=base/'previous.txt';ptmp=base/'previous.new';ptmp.write_text(name,encoding='ascii');os.replace(ptmp,prev)
            tmp=base/'current.new';tmp.write_text(release,encoding='ascii');os.replace(tmp,p)
        except Exception:
            for dest,_ in replacements:
                previous=old_files[dest]
                if previous is not None:
                    restore=base/(dest.name+'.restore');restore.write_bytes(previous);os.replace(restore,dest)
            (base/'current.txt').write_bytes(old_pointer)
            raise
        return release,'Aggiornamento '+m['version']+' installato',True
    except Exception as exc:
        # An unavailable or invalid update never blocks the last verified local version.
        log=base/'update.log'
        try:
            log.write_text(time.strftime('%Y-%m-%d %H:%M:%S')+' · '+str(exc)[:500],encoding='utf-8')
        except OSError:pass
        return name,'Aggiornamento non disponibile: uso la versione '+installed,False

def check_and_update(base):
    """Serialize checks so two double-clicks cannot race to replace the release pointer."""
    base=Path(base).resolve();base.mkdir(parents=True,exist_ok=True)
    lock=(base/'.update.lock').open('a+b')
    try:
        lock.seek(0,os.SEEK_END)
        if lock.tell()==0:lock.write(b'0');lock.flush()
        lock.seek(0)
        if os.name=='nt':
            import msvcrt
            try:msvcrt.locking(lock.fileno(),msvcrt.LK_NBLCK,1)
            except OSError:
                name,_,_=_current(base);return name,'Controllo aggiornamenti già in corso',False
        else:
            import fcntl
            try:fcntl.flock(lock.fileno(),fcntl.LOCK_EX|fcntl.LOCK_NB)
            except OSError:
                name,_,_=_current(base);return name,'Controllo aggiornamenti già in corso',False
        return _check_and_update_locked(base)
    finally:lock.close()

def rollback(base):
    """Restore previous installation pointer when the new engine fails its startup check."""
    base=Path(base).resolve()
    try:
        name=(base/'previous.txt').read_text(encoding='ascii').strip()
        target=(base/'versions'/name).resolve()
        if target.parent!=(base/'versions').resolve() or not (target/'app.py').is_file():return False
        tmp=base/'current.rollback';tmp.write_text(name,encoding='ascii');os.replace(tmp,base/'current.txt')
        return True
    except OSError:return False
