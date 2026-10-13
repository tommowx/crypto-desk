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

# Each process receives history once and reuses indicators and compact signal masks.
_DATASETS={}
_PREPARED={}
_SIGNALS=None

def init_research_worker(datasets):
    global _DATASETS,_PREPARED,_SIGNALS
    from collections import OrderedDict
    _DATASETS={d['pair']:dict(d,rows=d['rows'][:int(len(d['rows'])*.8)],full_length=len(d['rows'])) for d in datasets}
    _PREPARED={};_SIGNALS=OrderedDict()

def rule_key(config):
    return (config['strategy'],config.get('fast'),config.get('slow'),config.get('lookback'),config.get('exit_ma'))

def run_cached_chunk(pair,indexed_configs):
    from batch_engine import signal_arrays
    d=_DATASETS[pair];rows=d['rows']
    if pair not in _PREPARED:
        _PREPARED[pair]=prepare(rows,sorted(set(range(5,101,5))|set(range(110,301,10))),list(range(5,101,5)))
    prepared=_PREPARED[pair];result=[]
    for index,c in indexed_configs:
        key=(pair,rule_key(c))
        if key not in _SIGNALS:
            _SIGNALS[key]=signal_arrays(rows,c,prepared)
            if len(_SIGNALS)>64:_SIGNALS.popitem(last=False)
        _SIGNALS.move_to_end(key);signals=_SIGNALS[key];folds=[]
        for a,b,e in windows(d['full_length']):
            folds.append(dict(train=evaluate(rows,c,a,b,prepared,signals=signals),test=evaluate(rows,c,b,e,prepared,signals=signals),train_from=rows[a][0],test_from=rows[b][0],test_to=rows[e-1][0]))
        result.append(dict(key=f'{pair}:{index}',pair=pair,symbol=d['symbol'],index=index,config=c,folds=folds,
                           mean_test=sum(f['test']['return_pct'] for f in folds)/4,positive_periods=sum(f['test']['return_pct']>0 for f in folds),test_trades=sum(f['test']['trades'] for f in folds)))
    return result

def system_resources():
    """Actual OS memory and CPU counters, without an extra runtime dependency."""
    import os,time
    result=dict(logical_cpus=os.cpu_count() or 1,cpu_percent=None,total_memory=None,available_memory=None)
    if os.name=='nt':
        import ctypes
        from ctypes import wintypes
        class Memory(ctypes.Structure):
            _fields_=[('length',wintypes.DWORD),('load',wintypes.DWORD),('total',ctypes.c_ulonglong),('available',ctypes.c_ulonglong),('page_total',ctypes.c_ulonglong),('page_available',ctypes.c_ulonglong),('virtual_total',ctypes.c_ulonglong),('virtual_available',ctypes.c_ulonglong),('extended',ctypes.c_ulonglong)]
        memory=Memory();memory.length=ctypes.sizeof(memory)
        if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(memory)):
            result.update(total_memory=memory.total,available_memory=memory.available,memory_percent=memory.load)
        idle=wintypes.FILETIME();kernel=wintypes.FILETIME();user=wintypes.FILETIME()
        if ctypes.windll.kernel32.GetSystemTimes(ctypes.byref(idle),ctypes.byref(kernel),ctypes.byref(user)):
            value=lambda f:(f.dwHighDateTime<<32)|f.dwLowDateTime
            now=(value(idle),value(kernel)+value(user),time.monotonic())
            previous=getattr(system_resources,'previous',None)
            if previous and now[1]>previous[1]:result['cpu_percent']=round(100*(1-(now[0]-previous[0])/(now[1]-previous[1])),1)
            if not previous or now[2]-previous[2]>=1:system_resources.previous=now
    return result

def worker_budget():
    r=system_resources();cpu=max(1,r['logical_cpus']-2)
    # Leave memory for Windows, the dashboard and SQLite; indicators are reused per worker.
    memory=max(1,int((r['available_memory']-768*1024**2)//(320*1024**2))) if r['available_memory'] else cpu
    return max(1,min(cpu,memory,12))

def learned_signal(rows,config):
    n=max(config.get('slow',0),config.get('fast',0),config.get('lookback',0)+1,config.get('exit_ma',0))
    if len(rows)<n:return False,False,'Storico insufficiente per la regola appresa'
    closes=[r[4] for r in rows]
    if config['strategy']=='trend':
        fast=sum(closes[-config['fast']:])/config['fast'];slow=sum(closes[-config['slow']:])/config['slow']
        enter=closes[-1]>fast>slow
        return enter,not enter,f"Trend appreso {config['fast']}/{config['slow']}"
    level=max(r[2] for r in rows[-config['lookback']-1:-1]);ma=sum(closes[-config['exit_ma']:])/config['exit_ma']
    return closes[-1]>level,closes[-1]<ma,f"Breakout appreso {config['lookback']}/{config['exit_ma']}"

def portfolio_replay(datasets,candidates,start_fraction=.8,end_fraction=1.,cost_scenario='base'):
    """One continuous spot portfolio; past-only signals, next-open fills and real costs."""
    import time
    from batch_engine import COSTS,signal_arrays
    started=time.perf_counter();fee,slip=COSTS[cost_scenario];by_pair={d['pair']:d for d in datasets}
    chosen=[c for c in candidates if c['pair'] in by_pair]
    if not datasets:return None
    n=len(datasets[0]['rows']);start=max(300,int(n*start_fraction));end=min(n,int(n*end_fraction))
    if end<=start:raise ValueError('Periodo replay insufficiente')
    for d in datasets:
        if len(d['rows'])!=n or any(r[0]!=datasets[0]['rows'][i][0] for i,r in enumerate(d['rows'])):raise ValueError('Storici non sincronizzati')
    masks={}
    for c in chosen:
        rows=by_pair[c['pair']]['rows'];config=c['config']
        prep=prepare(rows,sorted({config.get('fast',20),config.get('slow',50),config.get('exit_ma',20)}),[config.get('lookback',20)])
        masks[c['pair']]=signal_arrays(rows,config,prep)
    cash=peak=1000.;positions={};trades=[];points=[];fees=dd=0.;ruin_at=None;exhausted_at=None;minimum=1000.
    def sell(pair,price,stamp,reason):
        nonlocal cash,fees
        p=positions.pop(pair);fill=price*(1-slip);gross=p['qty']*fill;exit_fee=gross*fee;net=gross-exit_fee;cash+=net;fees+=exit_fee
        trades.append(dict(time=stamp,pair=pair,symbol=by_pair[pair]['symbol'],side='SELL',price=fill,qty=p['qty'],fee=exit_fee,pnl=net-p['cost'],reason=reason,balance=cash))
    for i in range(start,end):
        stamp=datasets[0]['rows'][i][0];exited=set()
        for pair,p in list(positions.items()):
            op=by_pair[pair]['rows'][i][1]
            if op<=p['stop'] or masks[pair][1][i-1]:sell(pair,op,stamp,'Gap sotto stop' if op<=p['stop'] else 'Segnale terminato');exited.add(pair)
        equity=cash+sum(p['qty']*by_pair[k]['rows'][i][1]*(1-slip)*(1-fee) for k,p in positions.items())
        for c in chosen:
            pair=c['pair'];r=by_pair[pair]['rows'][i]
            if pair in positions or pair in exited or len(positions)>=2 or not masks[pair][0][i-1]:continue
            spend=min(max(0,equity*.20),cash)
            if spend<10:continue
            fill=r[1]*(1+slip);qty=spend/(fill*(1+fee));entry_fee=qty*fill*fee;cash-=spend;fees+=entry_fee
            positions[pair]=dict(qty=qty,cost=spend,stop=fill*(1-c['config']['stop']))
            trades.append(dict(time=stamp,pair=pair,symbol=by_pair[pair]['symbol'],side='BUY',price=fill,qty=qty,fee=entry_fee,pnl=None,reason='Regola selezionata sullo sviluppo',balance=cash))
        for pair,p in list(positions.items()):
            r=by_pair[pair]['rows'][i]
            if r[3]<=p['stop']:sell(pair,min(r[1],p['stop']),stamp,'Stop intrabar')
        equity=cash+sum(p['qty']*by_pair[k]['rows'][i][4]*(1-slip)*(1-fee) for k,p in positions.items())
        peak=max(peak,equity);minimum=min(minimum,equity);dd=max(dd,(peak-equity)/peak)
        points.append(dict(time=stamp,equity=equity,cash=cash,positions=len(positions)))
        if equity<=0:ruin_at=stamp;break
        if equity<10 and not positions:exhausted_at=stamp;break
    stamp=points[-1]['time']
    for pair in list(positions):sell(pair,by_pair[pair]['rows'][min(i,end-1)][4],stamp,'Fine periodo')
    points[-1].update(equity=cash,cash=cash,positions=0);minimum=min(minimum,cash);dd=max(dd,(peak-cash)/peak)
    closed=[t for t in trades if t['side']=='SELL'];historical_seconds=stamp-points[0]['time']+3600
    return dict(id=f'{stamp}:{start_fraction}:{cost_scenario}:{len(trades)}:{cash:.8f}',currency='USDT',initial_balance=1000.,final_balance=cash,return_pct=(cash/1000-1)*100,
                minimum_balance=minimum,drawdown_pct=dd*100,fees=fees,trade_count=len(closed),win_rate=sum(t['pnl']>0 for t in closed)/len(closed)*100 if closed else None,
                historical_seconds=historical_seconds,compute_seconds=round(time.perf_counter()-started,4),ruin_at=ruin_at,exhausted_at=exhausted_at,
                from_time=points[0]['time'],to_time=stamp,points=points,trades=trades,candidates=[dict(pair=c['pair'],symbol=c['symbol'],config=c['config']) for c in chosen],
                segment='verifica finale' if start_fraction>=.8 else 'allenamento',cost_scenario=cost_scenario,
                stop_reason='Saldo esaurito' if ruin_at or exhausted_at else 'Fine dei dati storici',method='Spot, massimo 2 posizioni, 20% per ingresso, segnali su chiusura precedente; costi inclusi. Il saldo non viene resettato durante il periodo.')
