"""Time-budgeted two-worker research. Committed results resume without repetition."""
from concurrent.futures import ProcessPoolExecutor,wait,FIRST_COMPLETED
import hashlib,json,multiprocessing,os,sqlite3,threading,time
from pathlib import Path
from deep_engine import deep_configs,run_deep_chunk,run_cached_chunk,init_research_worker,worker_budget,portfolio_replay,train_score,windows,VERSION
from batch_engine import evaluate,prepare
from history_data import load_history,ResearchStopped,read_plan,new_plan

class DeepLab:
    def __init__(self,data):
        self.data=Path(data);self.lock=threading.RLock();self.cancel=threading.Event()
        self.state=dict(status='idle',phase='In attesa',done=0,total=1_000_000,elapsed=0,budget_minutes=30,download_done=0,download_total=120,top=[],walk_forward=[],error=None,report=None,replay=None,progress_at=None)
        self.on_finished=None
        self.state.update(arena=None,validation=[],approved=[],workers=0,chunk_size=32)
        try:
            saved=self.data/'last_session.json'
            if saved.is_file():
                self.state.update(json.loads(saved.read_text(encoding='utf-8')))
                if self.state['status'] in ('running','cancelling'):
                    self.state.update(status='paused',phase='Sessione salvata: puoi riprendere')
            else:
                reports=sorted((self.data/'deep_research').glob('*/report.json'),key=lambda p:p.stat().st_mtime)
                if reports:
                    path=reports[-1];r=json.loads(path.read_text(encoding='utf-8'))
                    self.state.update(status=r['status'],done=r['completed_evaluations'],total=r['target_evaluations'],elapsed=r['elapsed_session_seconds'],top=r['top'],walk_forward=r['walk_forward'],datasets=r['datasets'],report=str(path),phase='Risultati salvati · avvia per la verifica storica')
        except (OSError,ValueError,KeyError) as e:
            self.state['restore_warning']=str(e)
    def snapshot(self):
        with self.lock:
            out=dict(self.state)
            if out.get('arena'):
                arena=dict(out['arena']);points=arena['points'];stride=max(1,(len(points)+799)//800)
                arena['points']=points[::stride]
                if arena['points'][-1]!=points[-1]:arena['points'].append(points[-1])
                arena['trades']=arena['trades'][-500:];out['arena']=arena
            plan=read_plan(self.data)
            out['plan']=plan
            out['coin_count']=len(plan['symbols']) if plan else 5
            out['worker_alive']=bool(getattr(self,'thread',None) and self.thread.is_alive())
            if out['status']=='idle': out['total']=out['coin_count']*200_000
            if out['status'] in ('running','cancelling'): out['elapsed']=round(time.monotonic()-self.started,1)
            out['evaluations_per_second']=round(max(0,out['done']-out.get('resumed',0))/max(.01,out['elapsed']),1)
            out['active_workers']=out.get('workers',0) if out['worker_alive'] else 0
            return out
    def active(self): return self.snapshot()['status'] in ('running','cancelling')
    def select(self,count):
        with self.lock:
            if self.state['status'] in ('running','cancelling'): raise ValueError('Interrompi e salva prima di cambiare monete')
            plan=new_plan(self.data,count)
            self.state.update(status='idle',phase='Estrazione salvata: pronta per la ricerca',done=0,total=count*200_000,top=[],walk_forward=[],report=None,replay=None,arena=None,validation=[],approved=[],error=None,download_done=0,download_total=count*24,resumed=0,datasets=[])
            return plan
    def start(self,minutes):
        if minutes not in (5,15,30,'auto'): raise ValueError('Scegli automatico oppure 5, 15 o 30 minuti')
        with self.lock:
            if self.state['status'] in ('running','cancelling'): raise ValueError('Ricerca già in corso')
            self.cancel.clear();self.started=time.monotonic();self.deadline=float('inf') if minutes=='auto' else self.started+minutes*60
            plan=read_plan(self.data) or new_plan(self.data,5)
            self.state.update(status='running',phase='Preparazione storico Binance / USDT',done=0,total=len(plan['symbols'])*200_000,resumed=0,datasets=[],elapsed=0,budget_minutes=None if minutes=='auto' else minutes,automatic=minutes=='auto',download_done=0,download_total=len(plan['symbols'])*len(plan['months']),top=[],walk_forward=[],error=None,report=None,replay=None,arena=None,validation=[],progress_at=time.time())
            self.thread=threading.Thread(target=self._run,daemon=False);self.thread.start()
    def stop(self):
        with self.lock:
            if self.state['status']=='running': self.cancel.set();self.state['status']='cancelling'
    def stopped(self): return self.cancel.is_set() or time.monotonic()>=self.deadline
    def progress(self,done,total,message):
        with self.lock: self.state.update(download_done=done,download_total=total,phase='Storico: '+message,progress_at=time.time())

    def _candidates(self,best):
        # Freeze one rule per asset from the LAST DEVELOPMENT TRAINING fold, never the holdout.
        selected=[]
        for (pair,i),(_,r) in sorted(best.items()):
            if i==3:selected.append(dict(pair=pair,symbol=r['symbol'],index=r['index'],config=r['config'],training=r['folds'][3]['train'],development_positive_periods=r['positive_periods']))
        return sorted(selected,key=lambda c:(-(c['training']['return_pct']-.5*c['training']['drawdown_pct']),c['pair']))

    def _validate_final(self,datasets,candidates,folder):
        frozen=[dict(pair=c['pair'],index=c['index'],config=c['config']) for c in candidates]
        signature=hashlib.sha256(json.dumps(frozen,sort_keys=True).encode()).hexdigest()
        path=folder/'validation.json'
        if path.exists():
            cached=json.loads(path.read_text(encoding='utf-8'))
            if cached['signature']!=signature:raise ValueError('Verifica finale già usata con altre regole: occorre un nuovo periodo indipendente')
            cached['reused']=True;return cached
        checks=[];by_pair={d['pair']:d for d in datasets}
        for c in candidates:
            rows=by_pair[c['pair']]['rows'];config=c['config'];a=int(len(rows)*.8)
            prepared=prepare(rows,sorted({config.get('fast',20),config.get('slow',50),config.get('exit_ma',20)}),[config.get('lookback',20)])
            base=evaluate(rows,config,a,len(rows),prepared)
            stress=evaluate(rows,dict(config,cost='stress'),a,len(rows),prepared)
            reasons=[]
            if c['training']['return_pct']<=0:reasons.append('sviluppo non positivo')
            if c['development_positive_periods']<3:reasons.append('meno di 3 periodi di sviluppo positivi')
            if base['trades']<3:reasons.append('meno di 3 operazioni nella verifica')
            if base['return_pct']<=0 or stress['return_pct']<=0:reasons.append('verifica con costi non positiva')
            if stress['drawdown_pct']>15:reasons.append('perdita dal picco oltre 15%')
            checks.append(dict(c,holdout=base,stress=stress,approved=not reasons,reasons=reasons))
        arena=portfolio_replay(datasets,candidates)
        stress_arena=portfolio_replay(datasets,candidates,cost_scenario='stress')
        approved=[c for c in checks if c['approved']]
        # The combined portfolio must also pass; individual results cannot simply be added.
        qualified_arena=portfolio_replay(datasets,approved,cost_scenario='stress') if approved else None
        if qualified_arena and (qualified_arena['return_pct']<=0 or qualified_arena['drawdown_pct']>15):
            for c in approved:c['approved']=False;c['reasons'].append('portafoglio combinato non supera la verifica')
            approved=[]
        result=dict(signature=signature,selected_before_holdout=frozen,checks=checks,arena=arena,stress_arena=stress_arena,qualified_arena=qualified_arena,approved=approved,reused=False)
        temp=path.with_suffix('.tmp');temp.write_text(json.dumps(result,ensure_ascii=False,allow_nan=False),encoding='utf-8');os.replace(temp,path)
        return result
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
            candidates=self._candidates(best)
            baseline=candidates or [dict(pair=d['pair'],symbol=d['symbol'],config=dict(strategy='trend',fast=20,slow=50,stop=.03,cost='base')) for d in datasets]
            with self.lock:self.state['arena']=portfolio_replay(datasets,baseline,.4,.8)
            ordered=sorted(enumerate(configs),key=lambda x:(x[1]['strategy'],x[1].get('fast',0),x[1].get('slow',0),x[1].get('lookback',0),x[1].get('exit_ma',0),x[1]['stop'],x[1]['cost']))
            def jobs():
                for i in range(0,len(ordered),32):
                    for d in datasets:
                        group=[(j,c) for j,c in ordered[i:i+32] if f"{d['pair']}:{j}" not in keys]
                        if group: yield d['pair'],group
            tasks=iter(jobs());exhausted=False
            workers=worker_budget()
            with self.lock:self.state.update(workers=workers,phase='Studio parallelo e allenamento storico')
            with ProcessPoolExecutor(max_workers=workers,mp_context=multiprocessing.get_context('spawn'),initializer=init_research_worker,initargs=(datasets,)) as pool:
                pending=set()
                while True:
                    while len(pending)<workers*2 and not exhausted and not self.stopped():
                        job=next(tasks,None)
                        if job is None: exhausted=True;break
                        pair,group=job;pending.add(pool.submit(run_cached_chunk,pair,group))
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
                            candidates=self._candidates(best)
                            if candidates:
                                arena=portfolio_replay(datasets,candidates,.4,.8)
                                with self.lock:self.state['arena']=arena
                            last_replay=time.monotonic()

                    # Finish only submitted chunks after a stop, then commit the partial session.
            walk=[]
            for (pair,i),(_,r) in sorted(best.items()):
                stress=db.execute('SELECT payload FROM results WHERE key=?',(f"{pair}:{r['index']+1}",)).fetchone()
                walk.append(dict(symbol=r['symbol'],period=i+1,config=r['config'],train=r['folds'][i]['train'],test=r['folds'][i]['test'],stress_test=json.loads(stress[0])['folds'][i]['test'] if stress else None))
            with self.lock:
                complete=self.state['done']==self.state['total'];status='completed' if complete else ('paused' if self.cancel.is_set() else 'budget')
                self.state.update(phase='Salvataggio riepilogo',walk_forward=walk)
            validation=None
            if complete and not self.cancel.is_set():
                with self.lock:self.state['phase']='Verifica finale su periodi separati'
                validation=self._validate_final(datasets,self._candidates(best),folder)
                with self.lock:self.state.update(arena=validation['arena'],validation=validation['checks'],approved=validation['approved'],holdout_reused=validation['reused'])
                policies=dict(schema=1,source_fingerprint=fingerprint,verified_at=time.time(),currency='USDT',target='solo simulazione Kraken EUR; mercato differente',approved=validation['approved'])
                target=self.data/'learned_rules.json';temp=target.with_suffix('.tmp');temp.write_text(json.dumps(policies,ensure_ascii=False),encoding='utf-8');os.replace(temp,target)
            report=folder/'report.json';tmp=folder/'report.tmp'
            summary=dict(version='0.5',selection_plan=read_plan(self.data),engine=VERSION,status=status,completed_evaluations=len(keys)*8,target_evaluations=len(datasets)*len(configs)*8,
                         resumed_evaluations=resumed,new_evaluations=len(keys)*8-resumed,all_four_positive=positive,parameter_coin_cost_results=len(keys),
                         input_sha256=fingerprint,elapsed_session_seconds=round(time.monotonic()-self.started,1),datasets=self.state['datasets'],top=top,walk_forward=walk,
                         full_results_file='checkpoint.sqlite3',methodology='12,500 rules x 2 costs x N selected coins x 4 folds x 2 windows = 200,000 evaluations per coin, not independent strategies. 24 full months Binance spot USDT, not Kraken EUR. Rolling earlier 40% training -> next 10% test, four periods, last 20% excluded. Train-only selection: return minus 0.5*drawdown, at least 20 trades. Top ranking uses test windows and is exploratory. Each window starts with 1000 USDT, 20% allocation. No combined portfolio, historical spread, delisted coins, taxes or 5% entry halt. All results stored in SQLite. No automatic strategy promotion. Repeated inspection is not independent validation.')
            summary.update(application_version='0.9.0',workers=workers,chunk_size=32,validation=validation,
                           methodology='Development: first 80%, four rolling folds. One rule per asset frozen from last training fold using return minus 0.5*drawdown and at least 20 trades. Final 20% checked once for base and stressed costs; repeated identical checks reuse saved validation. Experimental continuous portfolio includes rejected rules and is visibly separate from approved paper rules. Maximum two positions, 20% per entry, next-open execution. No leverage or real orders. Cash conservation alone is not a pass.')
            tmp.write_text(json.dumps(summary,ensure_ascii=False,allow_nan=False),encoding='utf-8');os.replace(tmp,report)
            with self.lock: self.state.update(status=status,phase='Completato' if complete else 'Salvato: Avvia / Riprendi per continuare',report=str(report),progress_at=time.time())
        except ResearchStopped:
            with self.lock: self.state.update(status='paused' if self.cancel.is_set() else 'budget',phase='Download sospeso; archivi completati conservati. Premi Avvia / Riprendi.',progress_at=time.time())
        except Exception as e:
            with self.lock: self.state.update(status='failed',error=str(e),phase='Errore: test già salvati conservati nel checkpoint',progress_at=time.time())
        finally:
            if db is not None: db.close()
            with self.lock:
                self.state['elapsed']=round(time.monotonic()-self.started,1)
                saved=dict(self.state)
            try:
                target=self.data/'last_session.json';temp=target.with_suffix('.tmp');temp.write_text(json.dumps(saved,ensure_ascii=False,allow_nan=False),encoding='utf-8');os.replace(temp,target)
                if self.on_finished:self.on_finished()
            except Exception as e:
                with self.lock:self.state['auto_export_error']=str(e)
    def export(self):
        with self.lock: name=self.state['report']
        if not name: raise ValueError('Report disponibile dopo la sessione o Interrompi e salva')
        return Path(name).read_bytes()
