import copy,math,tempfile,unittest,json,threading
from pathlib import Path
from unittest.mock import patch
from deep_engine import init_research_worker,run_cached_chunk,run_deep_chunk,portfolio_replay
from deep_jobs import DeepLab
import app

def dataset():
 rows=[]
 for i in range(1500):
  op=100+i*.02+5*math.sin(i/25);cl=op+.3*math.sin(i)
  rows.append([1700000000+i*3600,op,max(op,cl)+.2,min(op,cl)-.2,cl,100])
 return dict(pair='TESTUSDT',symbol='TEST/USDT',rows=rows)

class HistoricalTests(unittest.TestCase):
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
    self.assertEqual(len(calls),2);state=manager.snapshot();self.assertEqual(state['status'],'completed');self.assertEqual(state['kind'],'automatica');self.assertTrue(Path(state['path']).is_file());self.assertTrue((Path(tmp)/'ultimo.json').is_file())
