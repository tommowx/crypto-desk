"""Two-process, cancellable offline batch coordinator with reproducible reports."""
from concurrent.futures import ProcessPoolExecutor,as_completed
import hashlib,json,multiprocessing,os,threading,time
from pathlib import Path
from batch_engine import configurations,validate,run_chunk

class BatchLab:
    def __init__(self,data):
        self.data=Path(data);self.lock=threading.RLock();self.cancel=threading.Event()
        self.state=dict(status='idle',done=0,total=0,elapsed=0,workers=2,top=[],error=None,report=None,datasets=[])
    def snapshot(self):
        with self.lock:
            state=dict(self.state)
            if state['status'] in ('running','cancelling'): state['elapsed']=round(time.monotonic()-self.started,2)
            return state
    def start(self,analyses):
        with self.lock:
            if self.state['status'] in ('running','cancelling'): raise ValueError('Batch già in corso')
            datasets=[]
            for pair,a in list(analyses.items())[:5]:
                validate(a['rows'])
                datasets.append(dict(pair=pair,symbol=a['symbol'],rows=a['rows']))
            if not datasets: raise ValueError('Prima premi Analizza mercato e attendi il completamento')
            self.cancel.clear();self.started=time.monotonic()
            self.state=dict(status='running',done=0,total=len(datasets)*len(configurations()),elapsed=0,workers=2,top=[],error=None,report=None,
                            datasets=[dict(pair=d['pair'],symbol=d['symbol'],candles=len(d['rows']),start=d['rows'][0][0],end=d['rows'][-1][0],reserved=len(d['rows'])-int(len(d['rows'])*.8)) for d in datasets])
            self.thread=threading.Thread(target=self._run,args=(datasets,),daemon=False);self.thread.start()
    def stop(self):
        with self.lock:
            if self.state['status']=='running': self.cancel.set();self.state['status']='cancelling'
    def _run(self,datasets):
        results=[];error=None
        stamp=time.strftime('%Y%m%d_%H%M%S')+'_'+str(time.time_ns()%1000000)
        folder=self.data/'research';report_path=folder/('batch_'+stamp+'.json')
        try:
            folder.mkdir(parents=True,exist_ok=True)
            encoded=json.dumps(datasets,separators=(',',':'),allow_nan=False).encode()
            digest=hashlib.sha256(encoded).hexdigest()
            input_path=folder/('input_'+stamp+'.json');input_path.write_bytes(encoded)
            configs=configurations()
            with ProcessPoolExecutor(max_workers=2,mp_context=multiprocessing.get_context('spawn')) as pool:
                futures=[pool.submit(run_chunk,d['pair'],d['symbol'],d['rows'],configs[i:i+20]) for d in datasets for i in range(0,len(configs),20)]
                for future in as_completed(futures):
                    if self.cancel.is_set():
                        for pending in futures: pending.cancel()
                        break
                    results.extend(future.result())
                    with self.lock: self.state['done']=len(results)
            status='cancelled' if self.cancel.is_set() else 'completed'
            # This is a comparison window used for search, NOT independent validation.
            ranked=sorted(results,key=lambda r:r['comparison']['return_pct'],reverse=True)
            report=dict(version='0.3',status=status,created_at=time.time(),input_file=input_path.name,input_sha256=digest,
                        methodology='400 tests per coin: 200 parameter sets x 2 cost scenarios; 60% initial, 20% comparison, 20% reserved and not evaluated. Independent 20%-allocation accounts, not an aggregate portfolio. Present-day universe; exploratory selection, not proof of profitability.',
                        results=ranked)
            tmp=report_path.with_suffix('.tmp');tmp.write_text(json.dumps(report,ensure_ascii=False,allow_nan=False),encoding='utf-8');os.replace(tmp,report_path)
            with self.lock: self.state.update(status=status,top=ranked[:20],report=report_path.name)
        except Exception as exc:
            error=str(exc)
            with self.lock: self.state.update(status='failed',error=error)
        finally:
            with self.lock: self.state['elapsed']=round(time.monotonic()-self.started,2)
    def export(self):
        with self.lock: name=self.state['report']
        if not name: raise ValueError('Nessun report batch disponibile')
        return (self.data/'research'/name).read_bytes()
