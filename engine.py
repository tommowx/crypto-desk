"""Crypto Desk: paper-only engine, Python standard library."""
import json, math, time, urllib.request, urllib.parse
FEE = .004  # illustrative conservative fee per side, not an exchange tariff quote
SLIP = .001
UNIVERSE = {'XBT','BTC','ETH','SOL','XRP','ADA','DOGE','AVAX','LINK','DOT','LTC','BCH','ATOM','NEAR','UNI','AAVE','SUI','APT','ARB','OP','FIL'}

def api(endpoint, **params):
    url = 'https://api.kraken.com/0/public/' + endpoint + '?' + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers={'User-Agent':'CryptoDeskResearch/0.1'})
    with urllib.request.urlopen(req, timeout=20) as r: data = json.load(r)
    if data.get('error'): raise ValueError('; '.join(data['error']))
    return data['result']

def candles(pair):
    data = api('OHLC', pair=pair, interval=60)
    raw = next(v for k,v in data.items() if k != 'last')
    # Kraken always includes a last, unfinished candle. Never use it for signals.
    rows = [[int(r[0]), *map(float, r[1:5]), float(r[6])] for r in raw[:-1]]
    if len(rows) < 100: raise ValueError('Storico insufficiente: meno di 100 candele chiuse')
    for i,r in enumerate(rows):
        if not all(math.isfinite(x) for x in r) or min(r[1:5]) <= 0 or r[5] < 0: raise ValueError('Dati OHLC non validi')
        if r[2] < max(r[1],r[4]) or r[3] > min(r[1],r[4]): raise ValueError('OHLC incoerente')
        if i and r[0]-rows[i-1][0] != 3600: raise ValueError('Storico con intervalli mancanti')
    if time.time() - (rows[-1][0]+3600) > 7200: raise ValueError('Candele obsolete')
    return rows

def signal(rows, strategy='trend'):
    if len(rows) < 50: return False, 'Servono almeno 50 candele chiuse'
    close = [r[4] for r in rows]
    fast, slow = sum(close[-20:])/20, sum(close[-50:])/50
    if strategy == 'breakout':
        level = max(r[2] for r in rows[-21:-1])
        ok = close[-1] > level
        return ok, f'Chiusura {close[-1]:.5g}; massimo delle 20 ore precedenti {level:.5g}'
    return close[-1] > fast > slow, f'Chiusura {close[-1]:.5g}; media 20h {fast:.5g}; media 50h {slow:.5g}'

def backtest(rows, strategy, start, end):
    """Signal on closed bar i-1; execution at bar i open. Long-only, 20% sizing."""
    cash, pos, peak, dd, fees, wins = 1000., None, 1000., 0., 0., 0
    trades, curve = [], []
    def sell(price, ts, reason):
        nonlocal cash, pos, fees, wins
        fill = price*(1-SLIP); gross = pos['qty']*fill; cost = gross*FEE
        pnl = gross-cost-pos['cost']; cash += gross-cost; fees += cost; wins += int(pnl>0)
        trades.append({'entry_time':pos['time'],'time':ts,'entry':pos['entry'],'exit':fill,'pnl':pnl,'reason':reason})
        pos = None
    for i in range(max(50,start),end):
        ts, op, hi, lo, cl, vol = rows[i]
        wanted, _ = signal(rows[:i],strategy)
        if pos:
            if op <= pos['stop']: sell(op,ts,'Gap sotto stop')
            elif not wanted: sell(op,ts,'Segnale terminato')
        elif wanted:
            spend = cash*.20; fill = op*(1+SLIP); qty = spend/(fill*(1+FEE)); cost = qty*fill*FEE
            cash -= spend; fees += cost
            pos = {'qty':qty,'entry':fill,'cost':spend,'stop':fill*.97,'time':ts}
        if pos and lo <= pos['stop']: sell(min(op,pos['stop']),ts,'Stop 3%')
        equity = cash + (pos['qty']*cl*(1-SLIP)*(1-FEE) if pos else 0)
        peak = max(peak,equity); dd = max(dd,(peak-equity)/peak)
        curve.append([ts,equity])
    if pos: sell(rows[end-1][4],rows[end-1][0],'Fine finestra')
    if curve: curve[-1][1]=cash
    first=rows[max(50,start)][1]; last=rows[end-1][4]
    hold=1000/(first*(1+SLIP)*(1+FEE))*last*(1-SLIP)*(1-FEE)-1000
    return {'return_pct':(cash/1000-1)*100,'drawdown_pct':dd*100,'trades':len(trades),'win_rate':wins/len(trades)*100 if trades else None,'fees':fees,'hold_pct':hold/10,'curve':curve,'operations':trades,'from':rows[max(50,start)][0],'to':rows[end-1][0]}

def research(rows):
    split=int(len(rows)*.7)
    return {s:{'initial':backtest(rows,s,50,split),'validation':backtest(rows,s,split,len(rows))} for s in ('trend','breakout')}

def shortlist(pairs, tickers):
    out=[]
    for key,p in pairs.items():
        ws=p.get('wsname',''); base=ws.split('/')[0]
        if base not in UNIVERSE or not ws.endswith('/EUR') or p.get('status','online')!='online' or key not in tickers: continue
        t=tickers[key]; bid=float(t['b'][0]); ask=float(t['a'][0]); price=float(t['c'][0])
        if not all(math.isfinite(x) for x in (bid,ask,price)) or min(bid,ask,price)<=0 or ask<bid: continue
        volume=float(t['v'][1])*float(t['p'][1])
        if not math.isfinite(volume) or volume < 0: continue
        spread=(ask-bid)/((ask+bid)/2)*100
        eligible=volume>=100000 and spread<=.25
        score=math.log10(max(volume,1))*10-spread*20
        out.append({'pair':key,'symbol':ws.replace('XBT','BTC'),'price':price,'bid':bid,'ask':ask,'volume':volume,'spread':spread,'score':score,'eligible':eligible,'reason':'Liquidità e spread nei limiti' if eligible else 'Esclusa: volume < €100k/24h o spread > 0,25%'})
    return sorted(out,key=lambda x:x['score'],reverse=True)
