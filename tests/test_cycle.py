import copy
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch
import app


class CycleTests(unittest.TestCase):
    def setUp(self):
        self.old_state=copy.deepcopy(app.STATE); self.old_book=copy.deepcopy(app.BOOK)
        self.tmp=tempfile.TemporaryDirectory(); self.old_data=app.DATA; app.DATA=Path(self.tmp.name)
        app.STATE.update(busy=False,running=True,error=None,quotes_at=None)
        app.BOOK.update(cash=1000.,positions={},trades=[],seen={},peak=1000.,halted=False)
        self.pairs={'ETH':{'wsname':'ETH/EUR','status':'online'}}
        self.rows=[[int(time.time()//3600)*3600-3600,100,101,99,100,1]]
        self.calls=0
    def tearDown(self):
        app.DATA=self.old_data;self.tmp.cleanup()
        app.STATE.clear();app.STATE.update(self.old_state)
        app.BOOK.clear();app.BOOK.update(self.old_book)
    def api(self,endpoint):
        if endpoint=='AssetPairs': return self.pairs
        self.calls+=1
        price=100 if self.calls==1 else 120
        return {'ETH':{'b':[str(price)],'a':[str(price+.1)],'c':[str(price)],'v':['0','10000'],'p':['0','100']}}
    def run_cycle(self,api=None,candle_error=None):
        with patch.object(app,'api',side_effect=api or self.api), patch.object(app,'candles',return_value=self.rows,side_effect=candle_error), patch.object(app,'signal',return_value=(True,'test')), patch.object(app,'research',return_value={}), patch.object(app.time,'sleep'):
            app.cycle()
    def test_fills_use_refreshed_quote(self):
        self.run_cycle()
        self.assertEqual(self.calls,2)
        self.assertEqual(len(app.BOOK['trades']),1)
        self.assertAlmostEqual(app.BOOK['trades'][0]['price'],120.1*(1+app.SLIP))
        self.assertFalse(app.STATE['busy']);self.assertFalse(app.snapshot()['stale'])
    def test_failed_refresh_no_trade(self):
        def fail(endpoint):
            if endpoint=='Ticker' and self.calls==1: raise OSError('rete assente')
            return self.api(endpoint)
        self.run_cycle(api=fail)
        self.assertFalse(app.BOOK['trades']);self.assertTrue(app.STATE['error'])
        self.assertTrue(app.snapshot()['stale']);self.assertFalse(app.STATE['busy'])
    def test_failed_analysis_disclosed(self):
        self.run_cycle(candle_error=ValueError('OHLC assenti'))
        self.assertFalse(app.BOOK['trades']);self.assertEqual(len(app.STATE['warnings']),1)
    def test_scan_in_pause_does_not_trade(self):
        app.STATE['running']=False;self.run_cycle()
        self.assertFalse(app.BOOK['trades']);self.assertEqual(self.calls,1)

if __name__=='__main__': unittest.main()
