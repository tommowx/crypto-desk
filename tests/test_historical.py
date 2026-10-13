import copy,math,tempfile,unittest,json,threading,sqlite3,hashlib
from pathlib import Path
from contextlib import closing
from unittest.mock import patch
from deep_engine import extended_configs,deep_configs,VERSION,EXTENDED_VERSION,decode_result,init_research_worker,run_cached_chunk,run_deep_chunk,portfolio_replay
from deep_jobs import DeepLab
import app

def dataset():
 rows=[]
 for i in range(1500):
  op=100+i*.02+5*math.sin(i/25);cl=op+.3*math.sin(i)
  rows.append([1700000000+i*3600,op,max(op,cl)+.2,min(op,cl)-.2,cl,100])
 return dict(pair='TESTUSDT',symbol='TEST/USDT',rows=rows)

class HistoricalTests(unittest.TestCase):
 def test_saved_completion_does_not_start_work(self):
  with tempfile.TemporaryDirectory() as tmp:
   Path(tmp,'last_session.json').write_text(json.dumps(dict(status='completed',done=1000000,arena=dict(points=[dict(time=1,equity=1000)],trades=[]))))
   lab=DeepLab(tmp);self.assertFalse(lab.snapshot()['worker_alive']);self.assertFalse(lab.active())
 def test_new_parameter_blocks_are_unique_and_disjoint(self):
  first=extended_configs(0,12520);second=extended_configs(1,12520)
  self.assertEqual(first[:25000],deep_configs());self.assertEqual(len(first),25040)
  key=lambda c:json.dumps(c,sort_keys=True)
  self.assertEqual(len({key(c) for c in first}),len(first))
  self.assertTrue({key(c) for c in first[25000:]}.isdisjoint({key(c) for c in second[25000:]}))
 def test_extended_work_imports_old_but_counts_only_new(self):
  from concurrent.futures import ThreadPoolExecutor
  d=dataset();legacy=deep_configs();new=[dict(strategy='trend',fast=17,slow=113,stop=.0325,cost=c) for c in ('base','stress')];configs=legacy[:2]+new
  with tempfile.TemporaryDirectory() as tmp:
   root=Path(tmp);fingerprint=hashlib.sha256(json.dumps(dict(version=VERSION,manifest=[],configs=legacy),sort_keys=True).encode()).hexdigest()
   folder=root/'deep_research'/fingerprint;folder.mkdir(parents=True)
   with closing(sqlite3.connect(folder/'checkpoint.sqlite3')) as db:
    db.execute('CREATE TABLE results (key TEXT PRIMARY KEY,payload TEXT NOT NULL)')
    for r in run_deep_chunk(d['pair'],d['symbol'],d['rows'],list(enumerate(legacy[:2]))):db.execute('INSERT INTO results VALUES (?,?)',(r['key'],json.dumps(r)))
    db.commit()
   (root/'deep_plan.json').write_text(json.dumps(dict(symbols=[d['pair']],months=['test'])))
   policies=root/'learned_rules.json';policies.write_text('{"approved": []}')
   def executor(**kw):return ThreadPoolExecutor(max_workers=1,initializer=kw['initializer'],initargs=kw['initargs'])
   with patch('deep_jobs.load_history',return_value=([d],[])),patch('deep_jobs.extended_configs',return_value=configs),patch('deep_jobs.worker_budget',return_value=1),patch('deep_jobs.ProcessPoolExecutor',side_effect=executor):
    lab=DeepLab(root);lab.start('auto');lab.thread.join(10);state=lab.snapshot()
   self.assertEqual(state['status'],'completed',state.get('error'));self.assertEqual(state['resumed'],16);self.assertEqual(state['new_evaluations'],16);self.assertEqual(state['done'],32)
   self.assertEqual(policies.read_text(),'{"approved": []}');self.assertEqual(state['arena']['from_time'],d['rows'][600][0])
   with closing(sqlite3.connect(Path(state['report']).with_name('checkpoint.sqlite3'))) as db:
    self.assertTrue(all(isinstance(p,bytes) and decode_result(p)['key']==k for k,p in db.execute('SELECT key,payload FROM results')))
 def test_cached_results_equal_original(self):
  d=dataset();configs=[]
  for strategy in ('trend','breakout'):
   for cost in ('base','stress'):
    c=dict(strategy=strategy,stop=.03,cost=cost)
    c.update(dict(fast=20,slow=110) if strategy=='trend' else dict(lookback=20,exit_ma=20))
    configs.append((len(configs),c))
  init_research_worker([d]);self.assertEqual(run_deep_chunk(d['pair'],d['symbol'],d['rows'],configs),run_cached_chunk(d['pair'],configs))
 def test_future_candles_do_not_change_past_portfolio(self):
  d=dataset();c=dict(pair=d['pair'],symbol=d['symbol'],config=dict(strategy='trend',fast=20,slow=110,stop=.03,cost='base'))
  original=portfolio_replay([d],[c],.4,1)
  changed=copy.deepcopy(d)
  for row in changed['rows'][1300:]:
   for k in range(1,5):row[k]*=3
  future=portfolio_replay([changed],[c],.4,1)
  cutoff=d['rows'][1300][0]
  self.assertEqual([p for p in original['points'] if p['time']<cutoff],[p for p in future['points'] if p['time']<cutoff])
  self.assertEqual([t for t in original['trades'] if t['time']<cutoff],[t for t in future['trades'] if t['time']<cutoff])
  self.assertGreater(original['trade_count'],0);self.assertGreater(original['fees'],0)
  self.assertTrue(all(p['cash']>=0 and p['positions']<=2 for p in original['points']))
  self.assertAlmostEqual(original['final_balance'],1000+sum(t['pnl'] for t in original['trades'] if t['side']=='SELL'))
 def test_final_validation_reuses_frozen_selection(self):
  d=dataset();c=dict(pair=d['pair'],symbol=d['symbol'],index=0,config=dict(strategy='trend',fast=20,slow=110,stop=.03,cost='base'),training=dict(return_pct=1,drawdown_pct=1),development_positive_periods=2)
  with tempfile.TemporaryDirectory() as tmp:
   lab=DeepLab(tmp);first=lab._validate_final([d],[c],Path(tmp));second=lab._validate_final([d],[c],Path(tmp))
   self.assertFalse(first['approved']);self.assertTrue(second['reused'])
   c['index']=1
   with self.assertRaises(ValueError):lab._validate_final([d],[c],Path(tmp))
 def test_export_during_work_then_automatic_final_export(self):
  with tempfile.TemporaryDirectory() as tmp:
   started=threading.Event();release=threading.Event();calls=[]
   def export(path):
    calls.append(path);started.set()
    if len(calls)==1:release.wait(3)
    Path(path).write_bytes(b'archive')
   with patch.dict(app.os.environ,CRYPTO_DESK_EXPORT_DIR=tmp),patch.object(app,'export_all',side_effect=export):
    manager=app.ExportManager();manager.request();self.assertTrue(started.wait(1));manager.request('automatica');release.set();manager.wait()
    self.assertEqual(len(calls),2);state=manager.snapshot();self.assertEqual(state['status'],'completed');self.assertEqual(state['kind'],'automatica');self.assertGreaterEqual(state['automatic_finished_at'],state['automatic_started_at']);self.assertTrue(Path(state['path']).is_file());self.assertTrue((Path(tmp)/'ultimo.json').is_file())
