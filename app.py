import json, os, threading, time, secrets, webbrowser, io, sqlite3, tempfile, zipfile
from pathlib import Path
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse
from batch_jobs import BatchLab
from deep_jobs import DeepLab
from engine import api, candles, shortlist, research, signal, FEE, SLIP
from deep_engine import system_resources,learned_signal
ROOT=Path(__file__).resolve().parent
DATA=Path(os.environ.get('CRYPTO_DESK_DATA',str(ROOT/'data'))); DATA.mkdir(parents=True,exist_ok=True)
LAB=BatchLab(DATA); DEEP=DeepLab(DATA)
STOP_EVENT=threading.Event()
LOCK=threading.RLock(); TOKEN=secrets.token_urlsafe(24); PORT=8877
VERSION="0.9.3"; MAX_QUOTE_AGE=180
ROLES=['Scanner','Selezionatore','Analista','Critico','Rischio','Simulatore']
STATE={'session_started_at':None,'busy':False,'running':False,'stage':'Pronto','updated':None,'quotes_at':None,'warnings':[],'market':[],'analyses':{},'logs':[],'roles':{r:'In attesa' for r in ROLES},'error':None}
ROLE_ACTIVITY={r:dict(events=0,last_at=None) for r in ROLES}
WORKER_THREAD=None
BOOK={'cash':1000.,'positions':{},'trades':[],'seen':{},'peak':1000.,'halted':False}
if (DATA/'portfolio.json').exists():
    try: BOOK.update(json.loads((DATA/'portfolio.json').read_text(encoding='utf-8')))
    except Exception: raise SystemExit('Portfolio non leggibile. Conservare data/portfolio.json e chiedere assistenza; nessun reset automatico.')

def persist():
    temp=DATA/'portfolio.tmp'; temp.write_text(json.dumps(BOOK,ensure_ascii=False,indent=2),encoding='utf-8'); os.replace(temp,DATA/'portfolio.json')

def log(role,message):
    with LOCK:
        entry={'time':time.time(),'role':role,'text':message}; STATE['logs'].insert(0,entry); STATE['logs']=STATE['logs'][:250]
        STATE['roles'][role]=message
        if role in ROLE_ACTIVITY:ROLE_ACTIVITY[role].update(events=ROLE_ACTIVITY[role]['events']+1,last_at=entry['time'])
        with (DATA/'events.jsonl').open('a',encoding='utf-8') as f: f.write(json.dumps(entry,ensure_ascii=False)+'\n')

def equity(market):
    lookup={m['pair']:m for m in market}
    return BOOK['cash']+sum(p['qty']*lookup.get(k,{}).get('bid',p['mark'])*(1-SLIP)*(1-FEE) for k,p in BOOK['positions'].items())

def paper(market, analyses, quotes_at):
    lookup={m['pair']:m for m in market}
    with LOCK:
        age=time.time()-quotes_at if quotes_at is not None else float('inf')
        if not STATE['running'] or not 0 <= age <= MAX_QUOTE_AGE:
            log('Rischio','Operazioni sospese: pausa o quotazioni obsolete')
            return
        # Process exits first. No price: retain position and disclose stale valuation.
        for k,p in list(BOOK['positions'].items()):
            m=lookup.get(k)
            if not m: log('Rischio',f'{k}: quotazione assente; posizione non valutabile in tempo reale'); continue
            p['mark']=m['bid']; a=analyses.get(k)
            reason='Stop della posizione rilevato al controllo' if m['bid']<=p['stop'] else ('Segnale terminato' if a and a.get('exit_signal',not a['signal']) else None)
            if reason:
                fill=m['bid']*(1-SLIP); gross=p['qty']*fill; net=gross*(1-FEE); BOOK['cash']+=net
                BOOK['trades'].append({'time':time.time(),'symbol':m['symbol'],'side':'SELL','price':fill,'qty':p['qty'],'fee':gross*FEE,'pnl':net-p['cost'],'reason':reason})
                del BOOK['positions'][k]; log('Simulatore',f"Uscita {m['symbol']}: {reason}")
        eq=equity(market); BOOK['peak']=max(BOOK['peak'],eq)
        if eq<=BOOK['peak']*.95:
            BOOK['halted']=True; log('Rischio','Perdita dal picco ≥ 5%: nuove entrate bloccate. Uscite ancora monitorate.')
        if not BOOK['halted'] and STATE['running']:
            for k,a in analyses.items():
                if not STATE['running']: break
                if k in BOOK['positions'] or len(BOOK['positions'])>=2: continue
                m=lookup.get(k)
                if not m: continue
                stamp=a['rows'][-1][0]
                candle_age=time.time()-(stamp+3600)
                if not 0 <= candle_age <= 7200: continue
                if BOOK['seen'].get(k)==stamp: continue
                BOOK['seen'][k]=stamp
                if not a['signal'] or not m['eligible']: continue
                spend=min(eq*.20,BOOK['cash']); fill=m['ask']*(1+SLIP)
                if spend<10: continue
                qty=spend/(fill*(1+FEE)); BOOK['cash']-=spend
                BOOK['positions'][k]={'symbol':m['symbol'],'qty':qty,'entry':fill,'mark':m['bid'],'cost':spend,'stop':fill*(1-(a.get('rule') or {}).get('stop',.03)),'time':time.time(),'rule':a.get('rule')}
                BOOK['trades'].append({'time':time.time(),'symbol':m['symbol'],'side':'BUY','price':fill,'qty':qty,'fee':qty*fill*FEE,'pnl':None,'reason':a['reason']})
                log('Simulatore',f"Entrata virtuale {m['symbol']}: €{spend:.2f}")
        persist()

def cycle():
    with LOCK:
        if STATE['busy']: return
        STATE['busy']=True; STATE['error']=None; STATE['warnings']=[]; STATE['stage']='Scansione mercati'
    try:
        log('Scanner','Richiesta mercati e quotazioni pubbliche Kraken / EUR')
        pairs=api('AssetPairs'); ticks=api('Ticker'); market=shortlist(pairs,ticks)
        if not market: raise ValueError('Nessuna coppia EUR trovata nella lista configurata')
        quotes_at=time.time()
        with LOCK:
            STATE['market']=market; STATE['quotes_at']=quotes_at
        policies=read_policies()
        approved={r['symbol'].split('/')[0]:r for r in (policies or {}).get('approved',[])}
        chosen=[m for m in market if m['eligible'] and m['symbol'].split('/')[0] in approved] if policies else [m for m in market if m['eligible']][:5]
        with LOCK: held=set(BOOK['positions'])
        chosen += [m for m in market if m['pair'] in held and m not in chosen]
        log('Selezionatore',f'{len(market)} crypto controllate; {len(chosen)} da approfondire per volume e spread. Non è una previsione.')
        analyses={}
        for m in chosen:
            with LOCK: STATE['stage']='Analisi '+m['symbol']
            log('Analista','Candele orarie chiuse: '+m['symbol'])
            try:
                rows=candles(m['pair']);rule=approved.get(m['symbol'].split('/')[0],{}).get('config')
                held_rule=BOOK['positions'].get(m['pair'],{}).get('rule')
                rule=held_rule or rule
                if rule:wanted,leave,why=learned_signal(rows,rule)
                else:wanted,why=signal(rows);leave=not wanted
                # Existing positions retain their original exit rules; unapproved assets cannot enter.
                if m['symbol'].split('/')[0] not in approved:wanted=False
                analyses[m['pair']]={'symbol':m['symbol'],'rows':rows,'signal':wanted,'exit_signal':leave,'rule':rule,'reason':why,'tests':research(rows)}
                log('Critico',m['symbol']+': storico di circa 30 giorni; test preliminare, nessuna validazione per soldi reali.')
            except Exception as e:
                message=m['symbol']+': analisi scartata — '+str(e)
                with LOCK: STATE['warnings'].append(message)
                log('Critico',message)
            time.sleep(1.1)
        # Refresh executable quotes after the potentially long OHLC/research stage.
        with LOCK: running=STATE['running']
        if running:
            with LOCK: STATE['stage']='Aggiornamento quotazioni prima della simulazione'
            market=shortlist(pairs,api('Ticker'))
            if not market: raise ValueError('Nessuna quotazione valida per la simulazione')
            quotes_at=time.time()
        with LOCK:
            STATE['market']=market; STATE['quotes_at']=quotes_at
            STATE['analyses']=analyses
            STATE['updated']=time.time()
            if STATE['running']: paper(market,analyses,quotes_at)
            else: log('Simulatore','In pausa: analisi aggiornata, nessuna operazione')
        log('Rischio','Solo spot simulato. Massimo 2 posizioni, 20% ciascuna; commissione ipotizzata 0,40% e slippage 0,10% per lato.')
        with LOCK: STATE['stage']='Ciclo completato'
        if not approved:log('Simulatore','Nessuna regola ha superato la verifica finale: osservazione attiva, allenamento storico disponibile')
        elif not any(a['signal'] for a in analyses.values()):log('Simulatore','In attesa di un segnale: nessun ingresso conforme alle regole')
    except Exception as e:
        with LOCK: STATE['error']=str(e); STATE['stage']='Errore dati: nessuna nuova operazione'
        log('Scanner','Errore: '+str(e))
    finally:
        with LOCK: STATE['busy']=False

def worker():
    while not STOP_EVENT.is_set():
        with LOCK: run=STATE['running'] and not STATE['busy']
        if run: cycle()
        STOP_EVENT.wait(60)

def read_policies():
    path=DATA/'learned_rules.json'
    if not path.is_file():return None
    try:
        policy=json.loads(path.read_text(encoding='utf-8'))
        if policy.get('schema')!=1 or not isinstance(policy.get('approved'),list):raise ValueError('Regole non valide')
        return policy
    except (OSError,ValueError):return {'approved':[],'error':'Regole apprese non leggibili: ingressi sospesi'}

def module_audit(deep):
    arena=deep.get('arena') or {};validation=deep.get('validation',[])
    active=deep.get('worker_alive') and deep.get('status') in ('running','cancelling')
    tasks={
        'Scanner':('Dati integri e aggiornati',f"{deep.get('download_done',0)} / {deep.get('download_total',0)} archivi",'errore' if STATE['error'] else 'attivo' if active and deep.get('download_done',0)<deep.get('download_total',0) else 'verificato'),
        'Selezionatore':('Regole scelte sullo sviluppo',f"{len(validation)} candidate · {len(deep.get('approved',[]))} approvate",'in attesa' if not validation else 'verificato'),
        'Analista':('Calcolo delle strategie',f"{deep.get('done',0):,} valutazioni".replace(',','.'),'attivo' if active else 'completato' if deep.get('status')=='completed' else 'in pausa'),
        'Critico':('Verifica separata e costi stress',f"{len(validation)} regole controllate",'verificato' if validation else 'in attesa'),
        'Rischio':('Costi, capitale e perdite',f"Perdita dal picco {arena.get('drawdown_pct',0):.1f}%",'verificato' if arena else 'in attesa'),
        'Simulatore':('Portafoglio storico e paper',f"{arena.get('trade_count',0)} operazioni storiche",'attivo' if STATE['busy'] or active else 'in attesa del segnale' if STATE['running'] else 'in pausa')}
    return {role:dict(task=t[0],result=t[1],status=t[2],**ROLE_ACTIVITY[role]) for role,t in tasks.items()}

def snapshot():
    with LOCK:
        out=json.loads(json.dumps(STATE)); out['portfolio']=json.loads(json.dumps(BOOK)); out['equity']=equity(STATE['market'])
        out['batch']=LAB.snapshot(); out['deep']=DEEP.snapshot()
        out['token']=TOKEN; out['version']=VERSION
        out['resources']=system_resources();out['resources'].update(workers=out['deep'].get('active_workers',0),configured_workers=out['deep'].get('workers',0),evaluations_per_second=out['deep'].get('evaluations_per_second',0),gpu_used=False)
        out['module_audit']=module_audit(out['deep'])
        out['simulator_alive']=bool(WORKER_THREAD and WORKER_THREAD.is_alive())
        out['export_info']=EXPORTER.snapshot()
        out['learned_rules']=read_policies()
        age=time.time()-STATE['quotes_at'] if STATE['quotes_at'] is not None else float('inf')
        out['stale']=not 0 <= age <= MAX_QUOTE_AGE or bool(STATE['error'])
        out['unpriced']=[k for k in BOOK['positions'] if k not in {m['pair'] for m in STATE['market']}]
        return out

def export_all(destination=None):
    """Create one portable archive of the portfolio, research outputs and their inputs."""
    out=destination if destination is not None else io.BytesIO()
    with zipfile.ZipFile(out,'w',compression=zipfile.ZIP_DEFLATED,compresslevel=1) as archive:
        current=snapshot();current.pop('token',None)
        current['exported_at']=time.time();current['export_scope']='Risultati completati e checkpoint coerenti al momento della copia; la ricerca può continuare.'
        archive.writestr('Crypto_Desk/stato.json',json.dumps(current,ensure_ascii=False,indent=2))
        archive.writestr('Crypto_Desk/LEGGIMI.txt','Esportazione completa di Crypto Desk. Contiene stato e portafoglio simulato, eventi, piani e dati storici scaricati, report e risultati dettagliati. Nessun ordine reale.\n')
        for name in ('portfolio.json','events.jsonl','deep_plan.json','last_session.json','learned_rules.json'):
            path=DATA/name
            if path.is_file():archive.write(path,'Crypto_Desk/'+name,compress_type=zipfile.ZIP_DEFLATED)
        for folder,patterns in ((DATA/'deep_plans',('*',)),(DATA/'history_binance',('*',)),(DATA/'research',('*.json',)),(DATA/'deep_research',('**/manifest.json','**/report.json','**/validation.json'))):
            if not folder.is_dir():continue
            files=set()
            for pattern in patterns:files.update(folder.glob(pattern))
            if folder.name=='deep_research':files.update(folder.rglob('checkpoint.sqlite3'))
            for path in sorted(files):
                if not path.is_file() or path.name.endswith(('.tmp','.CHECKSUM.tmp')):continue
                rel=path.relative_to(DATA).as_posix();dest='Crypto_Desk/dati/'+rel
                if path.name=='checkpoint.sqlite3':
                    with tempfile.TemporaryDirectory(prefix='cryptodesk-export-') as temp:
                        copy=Path(temp)/'checkpoint.sqlite3'
                        src=sqlite3.connect(path.resolve().as_uri()+'?mode=ro',uri=True,timeout=10);dst=sqlite3.connect(copy)
                        try:src.backup(dst)
                        finally:dst.close();src.close()
                        archive.write(copy,dest,compress_type=zipfile.ZIP_DEFLATED)
                else:
                    kind=zipfile.ZIP_STORED if path.suffix.lower()=='.zip' else zipfile.ZIP_DEFLATED
                    archive.write(path,dest,compress_type=kind)
    return out.getvalue() if destination is None else destination

class ExportManager:
    def __init__(self):
        self.lock=threading.RLock();self.thread=None;self.pending_automatic=False
        self.directory=Path(os.environ.get('CRYPTO_DESK_EXPORT_DIR',str(Path.home()/'Documents'/'Crypto Desk'/'Esportazioni')))
        self.job=dict(status='idle',path=None,error=None)
        try:
            if (self.directory/'ultimo.json').is_file():self.job=json.loads((self.directory/'ultimo.json').read_text(encoding='utf-8'))
        except (OSError,ValueError):pass
    def snapshot(self):
        with self.lock:return dict(self.job,directory=str(self.directory))
    def request(self,kind='manuale'):
        with self.lock:
            if self.thread and self.thread.is_alive():
                if kind=='automatica':self.pending_automatic=True
                return dict(self.job)
            self.job=dict(status='saving',kind=kind,started_at=time.time(),path=None,error=None)
            self.thread=threading.Thread(target=self._save,daemon=False);self.thread.start()
            return dict(self.job)
    def _save(self):
        temp=None
        try:
            self.directory.mkdir(parents=True,exist_ok=True)
            name='Crypto_Desk_'+time.strftime('%Y-%m-%d_%H-%M-%S')+'_'+secrets.token_hex(2)+'.zip'
            path=self.directory/name;temp=path.with_suffix('.zip.tmp')
            export_all(temp);os.replace(temp,path)
            with self.lock:self.job.update(status='completed',path=str(path),finished_at=time.time(),bytes=path.stat().st_size)
            pointer=self.directory/'ultimo.tmp';pointer.write_text(json.dumps(self.snapshot(),ensure_ascii=False,indent=2),encoding='utf-8');os.replace(pointer,self.directory/'ultimo.json')
        except Exception as e:
            with self.lock:self.job.update(status='failed',error=str(e))
            if temp and temp.exists():temp.unlink()
        finally:
            with self.lock:
                again=self.pending_automatic;self.pending_automatic=False;self.thread=None
                if again:self.request('automatica')
    def wait(self):
        while True:
            with self.lock:thread=self.thread
            if thread is None:return
            thread.join()

EXPORTER=ExportManager()
DEEP.on_finished=lambda:EXPORTER.request('automatica')

class Handler(BaseHTTPRequestHandler):
    def send(self,code,data,kind='application/json'):
        raw=data if isinstance(data,bytes) else json.dumps(data,ensure_ascii=False).encode()
        self.send_response(code); self.send_header('Content-Type',kind+'; charset=utf-8'); self.send_header('Content-Length',str(len(raw))); self.send_header('Cache-Control','no-store'); self.send_header('X-Content-Type-Options','nosniff'); self.end_headers(); self.wfile.write(raw)
    def allowed(self): return self.headers.get('Host') in (f'127.0.0.1:{PORT}',f'localhost:{PORT}')
    def do_GET(self):
        if not self.allowed(): return self.send(403,{'error':'Host non consentito'})
        route=urlparse(self.path).path
        if route=='/': return self.send(200,(ROOT/'index.html').read_bytes(),'text/html')
        if route=='/api/state': return self.send(200,snapshot())
        if route=='/api/export/status':return self.send(200,EXPORTER.snapshot())
        if route=='/api/deep/export':
            try: return self.send(200,DEEP.export())
            except ValueError as e: return self.send(409,{'error':str(e)})
        if route=='/api/batch/export':
            try: return self.send(200,LAB.export())
            except ValueError as e: return self.send(409,{'error':str(e)})
        if route=='/api/export': return self.send(200,snapshot())
        if route=='/api/export/all': return self.send(200,export_all(),'application/zip')
        self.send(404,{'error':'Non trovato'})
    def do_POST(self):
        if not self.allowed() or self.headers.get('X-Desk-Token')!=TOKEN: return self.send(403,{'error':'Richiesta non autorizzata'})
        if self.headers.get('Origin') not in (f'http://127.0.0.1:{PORT}',f'http://localhost:{PORT}'): return self.send(403,{'error':'Origine non consentita'})
        if self.path=='/api/shutdown':
            with LOCK: STATE['running']=False; STATE['closing']=True
            STOP_EVENT.set();LAB.stop();DEEP.stop()
            self.send(200,{'ok':True})
            threading.Thread(target=self.server.shutdown,daemon=True).start()
            def close_window_after_response():
                time.sleep(.25)
                try:
                    from desktop_window import close_window
                    close_window(DATA)
                except Exception: pass
            threading.Thread(target=close_window_after_response,daemon=True).start()
            return
        if STATE.get('closing'): return self.send(409,{'error':'Chiusura in corso'})
        if self.path=='/api/export/start':return self.send(202,EXPORTER.request())
        if self.path.startswith('/api/deep/plan/'):
            try:
                with LOCK: plan=DEEP.select(int(self.path.rsplit('/',1)[1]))
                return self.send(200,{'ok':True,'symbols':plan['symbols']})
            except ValueError as e: return self.send(409,{'error':str(e)})
        if self.path in ('/api/deep/start5','/api/deep/start15','/api/deep/start30','/api/deep/startauto'):
            try:
                with LOCK:
                    if LAB.snapshot()['status'] in ('running','cancelling'): raise ValueError('Interrompi prima il batch rapido')
                    mode=self.path.rsplit('start',1)[1]
                    DEEP.start('auto' if mode=='auto' else int(mode))
                    STATE['session_started_at']=time.time()
                return self.send(200,{'ok':True})
            except ValueError as e: return self.send(409,{'error':str(e)})
        if self.path=='/api/deep/stop':
            DEEP.stop(); return self.send(200,{'ok':True})
        if self.path=='/api/batch/start':
            try:
                with LOCK:
                    if DEEP.active(): raise ValueError('Interrompi prima la ricerca approfondita')
                    analyses=json.loads(json.dumps(STATE['analyses']))
                    LAB.start(analyses)
                return self.send(200,{'ok':True})
            except ValueError as e: return self.send(409,{'error':str(e)})
        if self.path=='/api/batch/stop':
            LAB.stop(); return self.send(200,{'ok':True})
        with LOCK:
            if self.path=='/api/start':
                STATE['running']=True; log('Simulatore','Simulazione avviata; controlli ogni circa 60 secondi')
            elif self.path=='/api/pause':
                STATE['running']=False; log('Simulatore','Pausa completa: sospesi anche i controlli di uscita. Posizioni conservate.')
            elif self.path!='/api/scan': return self.send(404,{'error':'Non trovato'})
        if self.path!='/api/pause': threading.Thread(target=cycle,daemon=True).start()
        self.send(200,{'ok':True})
    def log_message(self,*args): pass

if __name__=='__main__':
    # Binding reserves port before background work; second instance exits without trading.
    try: server=ThreadingHTTPServer(('127.0.0.1',PORT),Handler)
    except OSError: raise SystemExit(f'Porta {PORT} occupata: chiudi la precedente istanza di Crypto Desk o il programma che usa questa porta. Nessun secondo bot avviato.')
    log('Simulatore','Avvio in PAUSA. Solo simulazione, nessuna chiave API richiesta.')
    WORKER_THREAD=threading.Thread(target=worker,daemon=True);WORKER_THREAD.start()
    print(f'Crypto Desk: http://127.0.0.1:{PORT} — Ctrl+C per chiudere')
    if '--no-browser' not in __import__('sys').argv:
        if '--desktop' in __import__('sys').argv:
            from desktop_window import open_window
            open_window(f'http://127.0.0.1:{PORT}',DATA)
        else: webbrowser.open(f'http://127.0.0.1:{PORT}')
    try: server.serve_forever()
    except KeyboardInterrupt:
        STOP_EVENT.set();LAB.stop();DEEP.stop()
    finally:
        STOP_EVENT.set(); LAB.stop(); DEEP.stop()
        server.server_close()
        # Keep the process alive until checkpoint/report writes have finished.
        for job in (LAB,DEEP):
            thread=getattr(job,'thread',None)
            if thread is not None: thread.join()
        EXPORTER.wait()

