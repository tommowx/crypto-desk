import unittest
from engine import backtest,signal,shortlist,FEE,SLIP

def sample(n=200):
    return [[i*3600,100+i,101+i,99+i,100+i,1000] for i in range(n)]

class Tests(unittest.TestCase):
    def test_flat_no_trades(self):
        rows=[[i*3600,100,100,100,100,1] for i in range(150)]
        r=backtest(rows,'trend',50,150)
        self.assertEqual(r['trades'],0); self.assertEqual(r['return_pct'],0)
    def test_signal_has_no_future(self):
        a=sample(); before=signal(a[:90]); a[90:]=[[i*3600,1,1,1,1,1] for i in range(90,200)]
        self.assertEqual(signal(a[:90]),before)
    def test_next_open_and_costs(self):
        r=backtest(sample(),'trend',50,51); t=r['operations'][0]
        self.assertAlmostEqual(t['entry'],150*(1+SLIP))
        expected=200/(150*(1+SLIP)*(1+FEE))*150*(1-SLIP)*(1-FEE)-200
        self.assertAlmostEqual(t['pnl'],expected); self.assertLess(t['pnl'],0)
    def test_gap_stop(self):
        a=sample(100); a[51]=[51*3600,100,102,98,100,1]
        r=backtest(a,'trend',50,52); self.assertEqual(r['operations'][0]['reason'],'Gap sotto stop')
        self.assertAlmostEqual(r['operations'][0]['exit'],100*(1-SLIP))
    def test_future_does_not_change_past_curve(self):
        a=sample(); r=backtest(a,'trend',50,200)
        b=sample(); b[150:]=[[i*3600,1,1,1,1,1] for i in range(150,200)]
        s=backtest(b,'trend',50,200)
        self.assertEqual(r['curve'][:100],s['curve'][:100])
    def test_bad_spread_excluded(self):
        p={'a':{'wsname':'ETH/EUR','status':'online'}}
        t={'a':{'a':['102'],'b':['100'],'c':['101'],'v':['1','2000'],'p':['1','101']}}
        self.assertFalse(shortlist(p,t)[0]['eligible'])

if __name__=='__main__': unittest.main()
