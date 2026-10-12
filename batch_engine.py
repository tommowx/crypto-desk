"""Offline parameter search. No network, portfolio writes, or live strategy changes."""
from collections import deque
from itertools import product
import math

COSTS={'base':(.004,.001),'stress':(.006,.002)}

def configurations():
    result=[]
    for fast,slow,stop,cost in product((5,10,15,20,30),(40,50,75,100),(.01,.02,.03,.04,.05),COSTS):
        result.append(dict(strategy='trend',fast=fast,slow=slow,stop=stop,cost=cost))
    for lookback,exit_ma,stop,cost in product((5,10,15,20,30),(5,10,15,20),(.01,.02,.03,.04,.05),COSTS):
        result.append(dict(strategy='breakout',lookback=lookback,exit_ma=exit_ma,stop=stop,cost=cost))
    return result

def validate(rows):
    if len(rows)<400: raise ValueError('Servono almeno 400 candele orarie chiuse per il batch')
    for i,r in enumerate(rows):
        if len(r)!=6 or not all(math.isfinite(v) for v in r): raise ValueError('Candela non valida')
        t,op,hi,lo,cl,vol=r
        if min(op,hi,lo,cl)<=0 or vol<0 or hi<max(op,cl) or lo>min(op,cl): raise ValueError('OHLC incoerente')
        if i and t-rows[i-1][0]!=3600: raise ValueError('Intervalli non regolari')

def prepare(rows, average_windows=None, high_windows=None):
    closes=[r[4] for r in rows]; prefix=[0.]
    for v in closes: prefix.append(prefix[-1]+v)
    averages={n:[None if i+1<n else (prefix[i+1]-prefix[i+1-n])/n for i in range(len(rows))] for n in (average_windows or (5,10,15,20,30,40,50,75,100))}
    highs={}
    for n in (high_windows or (5,10,15,20,30)):
        q=deque();values=[]
        for i,r in enumerate(rows):
            while q and q[0]<i-n: q.popleft()
            values.append(rows[q[0]][2] if i>=n else None)
            while q and rows[q[-1]][2]<=r[2]: q.pop()
            q.append(i)
        highs[n]=values
    return closes,averages,highs

def evaluate(rows,config,start,end,prepared=None,trace=False):
    """Signal i-1, fill at i open; no re-entry on same bar after an exit."""
    closes,averages,highs=prepared or prepare(rows)
    fee,slip=COSTS[config['cost']]
    cash=peak=1000.;qty=cost=stop=0.;dd=fees=0.;trades=wins=0
    points=[];events=[]
    def sell(price):
        nonlocal cash,qty,fees,trades,wins
        if trace: events.append(dict(time=rows[i][0],side='SELL',price=price*(1-slip)))
        gross=qty*price*(1-slip);exit_fee=gross*fee
        net=gross-exit_fee;cash+=net;fees+=exit_fee;trades+=1;wins+=int(net>cost);qty=0.
    for i in range(start,end):
        j=i-1;op,hi,lo,cl=rows[i][1:5]
        if config['strategy']=='trend':
            fast=averages[config['fast']][j];slow=averages[config['slow']][j]
            enter=fast is not None and slow is not None and closes[j]>fast>slow
            leave=not enter
        else:
            level=highs[config['lookback']][j];ma=averages[config['exit_ma']][j]
            enter=level is not None and closes[j]>level
            leave=ma is not None and closes[j]<ma
        if qty:
            if op<=stop: sell(op)
            elif leave: sell(op)
        elif enter:
            cost=cash*.20;fill=op*(1+slip);qty=cost/(fill*(1+fee))
            cash-=cost;fees+=qty*fill*fee;stop=fill*(1-config['stop'])
            if trace: events.append(dict(time=rows[i][0],side='BUY',price=fill))
        if qty and lo<=stop: sell(min(op,stop))
        equity=cash+qty*cl*(1-slip)*(1-fee)
        peak=max(peak,equity);dd=max(dd,(peak-equity)/peak)
        if trace: points.append(dict(time=rows[i][0],price=cl,equity=equity))
    if qty: sell(rows[end-1][4])
    metrics=dict(return_pct=(cash/1000-1)*100,drawdown_pct=dd*100,trades=trades,win_rate=wins/trades*100 if trades else None,fees=fees)

    if trace:
        points[-1]['equity']=cash
        metrics['points']=points;metrics['events']=events
    return metrics

def run_chunk(pair,symbol,rows,configs):
    # Last 20% deliberately excluded before any indicators or evaluation are calculated.
    train_end=int(len(rows)*.6);search_end=int(len(rows)*.8)
    visible=rows[:search_end];prepared=prepare(visible)
    return [dict(pair=pair,symbol=symbol,config=c,initial=evaluate(visible,c,100,train_end,prepared),comparison=evaluate(visible,c,train_end,search_end,prepared)) for c in configs]
