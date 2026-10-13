"""Time-budgeted two-worker research. Committed results resume without repetition."""
from concurrent.futures import ProcessPoolExecutor,wait,FIRST_COMPLETED
import hashlib,json,multiprocessing,os,sqlite3,threading,time
from pathlib import Path
from deep_engine import deep_configs,run_deep_chunk,train_score,windows,VERSION
from batch_engine import evaluate,prepare
from history_data import load_history,ResearchStopped,read_plan,new_plan

class DeepLab:
    def __init__(self,data):
        self.data=Path(data);self.lock=threading.RLock();self.cancel=threading.Event()
        self.state=dict(status='idle',phase='In attesa',done=0,total=1_000_000,elapsed=0,budget_minutes=30,download_done=0,download_total=120,top=[],walk_forward=[],error=None,report=None,replay=None,progress_at=None)
    def snapshot(self):
        with self.lock:
            out=dict(self.state)
            plan=read_plan(self.data)
            out['plan']=plan
            out['coin_count']=len(plan['symbols']) if plan else 5
            out['worker_alive']=bool(getattr(self,'thread',None) and self.thread.is_alive())
            if out['status']=='idle': out['total']=out['coin_count']*200_000
            if out['status'] in ('running','cancelling'): out['elapsed']=round(time.monotonic()-self.started,1)
            return out
    def active(self): return self.snapshot()['status'] in ('running','cancelling')
    def select(self,count):
        with self.lock:
            if self.state['status'] in ('running','cancelling'): raise ValueError('Interrompi e salva prima di cambiare monete')
            plan=new_plan(self.data,count)
            self.state.update(status='idle',phase='Estrazione salvata: pronta per la ricerca',done=0,total=count*200_000,top=[],walk_forward=[],report=None,replay=None,error=None,download_done=0,download_total=count*24,resumed=0,datasets=[])
            return plan
    def start(self,minutes):
        if minutes not in (5,15,30,'auto'): raise ValueError('Scegli automatico oppure 5, 15 o 30 minuti')
        with self.lock:
            if self.state['status'] in ('running','cancelling'): raise ValueError('Ricerca già in corso')
            self.cancel.clear();self.started=time.monotonic();self.deadline=float('inf') if minutes=='auto' else self.started+minutes*60
            plan=read_plan(self.data) or new_plan(self.data,5)
            self.state.update(status='running',phase='Preparazione storico Binance / USDT',done=0,total=len(plan['symbols'])*200_000,resumed=0,datasets=[],elapsed=0,budget_minutes=None if minutes=='auto' else minutes,automatic=minutes=='auto',download_done=0,download_total=len(plan['symbols'])*len(plan['months']),top=[],walk_forward=[],error=None,report=None,replay=None,progress_at=time.time())
            self.thread=threading.Thread(target=self._run,daemon=False);self.thread.start()
    def stop(self):
        with self.lock:
            if self.state['status']=='running': self.cancel.set();self.state['status']='cancelling'
    def stopped(self): return self.cancel.is_set() or time.monotonic()>=self.deadline
    def progress(self,done,total,message):
        with self.lock: self.state.update(download_done=done,download_total=total,phase='Storico: '+message,progress_at=time.time())
    def _run(self):
        db=None;best={};top=[];keys=set();positive=0;last_replay=-float("inf")
        def ingest(r):
            nonlocal positive
            positive+=int(r['positive_periods']==4)
            top.append(r);top.sort(key=lambda x:(x['positive_periods'],x['mean_test'],-x['index'],x['pair']),reverse=True);del top[20:]
            if r['config']['cost']=='base':
                for i,f in enumerate(r['folds']):
                    score=train_score(f['train'])
                    if score is None: continue
                    key=(r['pair'],i);candidate=(score,-r['index'])
                    if key not in best or candidate>best[key][0]: best[key]=(candidate,r)
        try:
            datasets,manifest=load_history(self.data,self.progress,self.stopped)
            configs=deep_configs()
            fingerprint=hashlib.sha256(json.dumps(dict(version=VERSION,manifest=manifest,configs=configs),sort_keys=True).encode()).hexdigest()
            folder=self.data/'deep_research'/fingerprint;folder.mkdir(parents=True,exist_ok=True)
            (folder/'manifest.json').write_text(json.dumps(dict(version=VERSION,sources=manifest,configs=configs),ensure_ascii=False),encoding='utf-8')
            db=sqlite3.connect(folder/'checkpoint.sqlite3');db.execute('CREATE TABLE IF NOT EXISTS results (key TEXT PRIMARY KEY, payload TEXT NOT NULL)');db.commit()
            with self.lock: self.state['phase']='Lettura dei risultati già salvati'
            for key,payload in db.execute('SELECT key,payload FROM results ORDER BY key'):
                keys.add(key);ingest(json.loads(payload))
            resumed=len(keys)*8
            with self.lock:
                self.state.update(done=resumed,resumed=resumed,total=len(datasets)*len(configs)*8,top=list(top),phase='Calcolo su quattro periodi progressivi',progress_at=time.time(),
                                  datasets=[dict(symbol=d['symbol'],candles=len(d['rows']),start=d['rows'][0][0],end=d['rows'][-1][0]) for d in datasets])
            def jobs():
                for i in range(0,len(configs),4):
                    for d in datasets:
                        group=[(j,configs[j]) for j in range(i,min(i+4,len(configs))) if f"{d['pair']}:{j}" not in keys]
                        if group: yield d,group
            tasks=iter(jobs());exhausted=False
            with ProcessPoolExecutor(max_workers=2,mp_context=multiprocessing.get_context('spawn')) as pool:
                pending=set()
                while True:
                    while len(pending)<4 and not exhausted and not self.stopped():
                        job=next(tasks,None)
                        if job is None: exhausted=True;break
                        d,group=job;pending.add(pool.submit(run_deep_chunk,d['pair'],d['symbol'],d['rows'],group))
                    if not pending: break
                    finished,_=wait(pending,timeout=.25,return_when=FIRST_COMPLETED)
                    for future in finished:
                        pending.remove(future);results=future.result()
                        with db:
                            for r in results: db.execute('INSERT INTO results VALUES (?,?)',(r['key'],json.dumps(r,allow_nan=False)))
                        for r in results: keys.add(r['key']);ingest(r)
                        with self.lock: self.state.update(done=len(keys)*8,top=list(top),progress_at=time.time())
                        if results and time.monotonic()-last_replay>=20:
                            r=results[0];d=next(d for d in datasets if d['pair']==r['pair']);c=r['config']
                            visible=d['rows'][:int(len(d['rows'])*.8)]
                            av=[c['fast'],c['slow']] if c['strategy']=='trend' else [c['exit_ma']]
                            prep=prepare(visible,av,[c.get('lookback',5)])
                            _,a,b=windows(len(d['rows']))[0]
                            trace=evaluate(visible,c,a,b,prep,trace=True)
                            points=trace.pop('points')[-240:];events=[e for e in trace.pop('events') if e['time']>=points[0]['time']]
                            with self.lock: self.state['replay']=dict(id=r['key'],symbol=r['symbol'],config=c,points=points,events=events,metrics=trace)
                            last_replay=time.monotonic()

                    # On stop/deadline finish only the at-most-four submitted chunks, then save.
            walk=[]
            for (pair,i),(_,r) in sorted(best.items()):
                stress=db.execute('SELECT payload FROM results WHERE key=?',(f"{pair}:{r['index']+1}",)).fetchone()
                walk.append(dict(symbol=r['symbol'],period=i+1,config=r['config'],train=r['folds'][i]['train'],test=r['folds'][i]['test'],stress_test=json.loads(stress[0])['folds'][i]['test'] if stress else None))
            with self.lock:
                complete=self.state['done']==self.state['total'];status='completed' if complete else ('paused' if self.cancel.is_set() else 'budget')
                self.state.update(phase='Salvataggio riepilogo',walk_forward=walk)
            report=folder/'report.json';tmp=folder/'report.tmp'
            summary=dict(version='0.5',selection_plan=read_plan(self.data),engine=VERSION,status=status,completed_evaluations=len(keys)*8,target_evaluations=len(datasets)*len(configs)*8,
                         resumed_evaluations=resumed,new_evaluations=len(keys)*8-resumed,all_four_positive=positive,parameter_coin_cost_results=len(keys),
                         input_sha256=fingerprint,elapsed_session_seconds=round(time.monotonic()-self.started,1),datasets=self.state['datasets'],top=top,walk_forward=walk,
                         full_results_file='checkpoint.sqlite3',methodology='12,500 rules x 2 costs x N selected coins x 4 folds x 2 windows = 200,000 evaluations per coin, not independent strategies. 24 full months Binance spot USDT, not Kraken EUR. Rolling earlier 40% training -> next 10% test, four periods, last 20% excluded. Train-only selection: return minus 0.5*drawdown, at least 20 trades. Top ranking uses test windows and is exploratory. Each window starts with 1000 USDT, 20% allocation. No combined portfolio, historical spread, delisted coins, taxes or 5% entry halt. All results stored in SQLite. No automatic strategy promotion. Repeated inspection is not independent validation.')
            tmp.write_text(json.dumps(summary,ensure_ascii=False,allow_nan=False),encoding='utf-8');os.replace(tmp,report)
            with self.lock: self.state.update(status=status,phase='Completato' if complete else 'Salvato: Avvia / Riprendi per continuare',report=str(report),progress_at=time.time())
        except ResearchStopped:
            with self.lock: self.state.update(status='paused' if self.cancel.is_set() else 'budget',phase='Download sospeso; archivi completati conservati. Premi Avvia / Riprendi.',progress_at=time.time())
        except Exception as e:
            with self.lock: self.state.update(status='failed',error=str(e),phase='Errore: test già salvati conservati nel checkpoint',progress_at=time.time())
        finally:
            if db is not None: db.close()
            with self.lock: self.state['elapsed']=round(time.monotonic()-self.started,1)
    def export(self):
        with self.lock: name=self.state['report']
        if not name: raise ValueError('Report disponibile dopo la sessione o Interrompi e salva')
        return Path(name).read_bytes()
