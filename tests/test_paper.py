import tempfile,unittest,time,copy
from pathlib import Path
from unittest.mock import patch
import app

class PaperTests(unittest.TestCase):
 def setUp(self):
  self.old_state=copy.deepcopy(app.STATE);self.old_book=copy.deepcopy(app.BOOK)
  self.tmp=tempfile.TemporaryDirectory(); self.old=app.DATA; app.DATA=Path(self.tmp.name)
  app.BOOK.clear();app.BOOK.update(cash=1000.,positions={},trades=[],seen={},peak=1000.,halted=False)
  app.STATE['running']=True
  self.m=[{'pair':'ETH','symbol':'ETH/EUR','bid':100.,'ask':100.1,'eligible':True}]
  self.a={'ETH':{'signal':True,'reason':'test','rows':[[int(time.time()//3600)*3600-3600,100,101,99,100,1]]}}
 def tearDown(self):
  app.DATA=self.old;self.tmp.cleanup();app.STATE.clear();app.STATE.update(self.old_state);app.BOOK.clear();app.BOOK.update(self.old_book)
 def test_idempotent_and_exit_costs(self):
  app.paper(self.m,self.a,time.time());app.paper(self.m,self.a,time.time())
  self.assertEqual(len(app.BOOK['trades']),1);self.assertAlmostEqual(app.BOOK['cash'],800)
  self.m[0]['bid']=90
  app.paper(self.m,self.a,time.time())
  self.assertEqual(len(app.BOOK['trades']),2);self.assertFalse(app.BOOK['positions'])
  self.assertLess(app.BOOK['cash'],1000)
 def test_paused_no_entry(self):
  app.STATE['running']=False;app.paper(self.m,self.a,time.time());self.assertFalse(app.BOOK['trades'])
 def test_drawdown_blocks_entry(self):
  app.BOOK['cash']=940;app.paper(self.m,self.a,time.time())
  self.assertTrue(app.BOOK['halted']);self.assertFalse(app.BOOK['positions'])
 def test_partial_fetch_no_entry(self):
  app.paper(self.m,{},time.time());self.assertFalse(app.BOOK['positions'])
 def test_stale_quotes_block_exits_and_entries(self):
  app.paper(self.m,self.a,time.time())
  self.m[0]['bid']=90
  before=copy.deepcopy(app.BOOK)
  app.paper(self.m,self.a,time.time()-181)
  self.assertEqual(app.BOOK,before)
 def test_pause_blocks_exits(self):
  app.paper(self.m,self.a,time.time());app.STATE['running']=False
  self.m[0]['bid']=90
  app.paper(self.m,self.a,time.time())
  self.assertEqual(len(app.BOOK['trades']),1)
 def test_missing_quote_does_not_crash(self):
  app.paper([],self.a,time.time());self.assertFalse(app.BOOK['trades'])
 def test_stale_signal_no_entry(self):
  self.a['ETH']['rows'][-1][0]=0
  app.paper(self.m,self.a,time.time());self.assertFalse(app.BOOK['trades'])
 def test_snapshot_uses_quote_age_not_cycle_time(self):
  app.STATE.update(updated=time.time(),quotes_at=time.time()-181,error=None)
  self.assertTrue(app.snapshot()['stale'])
if __name__=='__main__':unittest.main()

