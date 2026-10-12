"""Fixed search plan: 12,500 rules x two cost scenarios, four rolling folds."""
from itertools import product
import random
from batch_engine import prepare,evaluate
VERSION='deep-1'

def deep_configs():
    base=[];stops=[i/200 for i in range(1,21)]
    for fast,slow,stop in product(range(5,101,5),range(110,301,10),stops):
        base.append(dict(strategy='trend',fast=fast,slow=slow,stop=stop))
    for lookback,exit_ma,stop in product(range(5,101,5),range(5,101,5),stops):
        base.append(dict(strategy='breakout',lookback=lookback,exit_ma=exit_ma,stop=stop))
    random.Random(42103).shuffle(base)
    return [dict(c,cost=cost) for c in base[:12500] for cost in ('base','stress')]

def windows(n):
    if n<1000: raise ValueError('Storico insufficiente per la ricerca approfondita')
    return [(max(300,int(n*k/10)-int(n*.4)),int(n*k/10),int(n*(k+1)/10)) for k in (4,5,6,7)]

def train_score(metrics):
    return metrics['return_pct']-.5*metrics['drawdown_pct'] if metrics['trades']>=20 else None

def run_deep_chunk(pair,symbol,rows,indexed_configs):
    visible=rows[:int(len(rows)*.8)];av=set();high=set()
    for _,c in indexed_configs:
        av.update((c['fast'],c['slow']) if c['strategy']=='trend' else (c['exit_ma'],))
        if c['strategy']=='breakout': high.add(c['lookback'])
    prepared=prepare(visible,sorted(av),sorted(high) or [5]);result=[]
    for index,c in indexed_configs:
        folds=[]
        for a,b,e in windows(len(rows)):
            folds.append(dict(train=evaluate(visible,c,a,b,prepared),test=evaluate(visible,c,b,e,prepared),train_from=rows[a][0],test_from=rows[b][0],test_to=rows[e-1][0]))
        result.append(dict(key=f'{pair}:{index}',pair=pair,symbol=symbol,index=index,config=c,folds=folds,
                           mean_test=sum(f['test']['return_pct'] for f in folds)/4,positive_periods=sum(f['test']['return_pct']>0 for f in folds),test_trades=sum(f['test']['trades'] for f in folds)))
    return result
