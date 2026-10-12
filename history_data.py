"""Public monthly spot archives, checked and cached without API credentials."""
import calendar,csv,hashlib,io,json,os,random,time,urllib.request,zipfile
from datetime import datetime,timezone
from pathlib import Path
from batch_engine import validate
SYMBOLS=('BTCUSDT','ETHUSDT','SOLUSDT','XRPUSDT','NEARUSDT')
POOL=tuple(x+'USDT' for x in ('BTC','ETH','SOL','XRP','ADA','DOGE','AVAX','LINK','DOT','LTC','BCH','ATOM','NEAR','UNI','AAVE','SUI','APT','ARB','OP','FIL'))
BASE='https://data.binance.vision/data/spot/monthly/klines/'
class ResearchStopped(Exception): pass

def months_before(now=None,count=24):
    now=now or datetime.now(timezone.utc);m=now.year*12+now.month-1
    return [f'{i//12:04d}-{i%12+1:02d}' for i in range(m-count,m)]

def read_plan(data):
    path=Path(data)/'deep_plan.json'
    if not path.exists(): return None
    plan=json.loads(path.read_text(encoding='utf-8'))
    # Preserve the five fixed assets of existing v0.4 plans.
    plan.setdefault('symbols',list(SYMBOLS));plan.setdefault('selection','legacy_fixed')
    return plan

def new_plan(data,count):
    if type(count) is not int or not 1<=count<=len(POOL): raise ValueError('Scegli da 1 a 20 monete')
    data=Path(data);data.mkdir(parents=True,exist_ok=True)
    previous=read_plan(data)
    plan={'months':previous['months'] if previous else months_before(),
          'symbols':sorted(random.SystemRandom().sample(POOL,count)),
          'selection':'random_from_configured_20','created_at':time.time()}
    archive=data/'deep_plans';archive.mkdir(exist_ok=True)
    if previous:
        (archive/(str(time.time_ns())+'_previous.json')).write_text(json.dumps(previous),encoding='utf-8')
    target=data/'deep_plan.json';tmp=target.with_suffix('.tmp');tmp.write_text(json.dumps(plan),encoding='utf-8');os.replace(tmp,target)
    return plan

def fetch(url,limit):
    req=urllib.request.Request(url,headers={'User-Agent':'CryptoDeskResearch/0.4'})
    with urllib.request.urlopen(req,timeout=15) as r: raw=r.read(limit+1)
    if len(raw)>limit: raise ValueError('Archivio troppo grande')
    return raw

def parse_month(raw,month):
    year,mon=map(int,month.split('-'))
    first=int(datetime(year,mon,1,tzinfo=timezone.utc).timestamp());count=calendar.monthrange(year,mon)[1]*24
    with zipfile.ZipFile(io.BytesIO(raw)) as z:
        info=z.infolist()
        if len(info)!=1 or info[0].file_size>5_000_000: raise ValueError('Archivio inatteso')
        text=z.read(info[0]).decode('utf-8-sig')
    rows=[]
    for r in csv.reader(io.StringIO(text)):
        if not r: continue
        if not rows and r[0].lower() in ('open_time','open time'): continue
        t=int(r[0]);unit=1_000_000 if t>=100_000_000_000_000 else 1000
        if t%unit: raise ValueError('Timestamp non allineato')
        rows.append([t//unit,*map(float,r[1:6])])
    validate(rows)
    if len(rows)!=count or rows[0][0]!=first or rows[-1][0]!=first+(count-1)*3600:
        raise ValueError('Mese incompleto: nessun riempimento artificiale')
    return rows

def load_history(data,progress,stopped):
    data=Path(data);data.mkdir(parents=True,exist_ok=True)
    plan=read_plan(data) or new_plan(data,5)
    months=plan['months'];symbols=plan['symbols']
    cache=data/'history_binance';cache.mkdir(exist_ok=True)
    datasets=[];manifest=[];done=0
    for symbol in symbols:
        rows=[]
        for month in months:
            if stopped(): raise ResearchStopped()
            name=f'{symbol}-1h-{month}.zip';path=cache/name;check=cache/(name+'.CHECKSUM');url=BASE+symbol+'/1h/'+name
            progress(done,len(symbols)*len(months),f'{symbol} · {month}')
            try:
                if path.exists() and check.exists(): raw=path.read_bytes();expected=check.read_text().split()[0]
                else:
                    expected=fetch(url+'.CHECKSUM',2048).decode().split()[0]
                    if stopped(): raise ResearchStopped()
                    raw=fetch(url,5_000_000)
                if len(expected)!=64 or hashlib.sha256(raw).hexdigest()!=expected: raise ValueError('Checksum non valido')
                parsed=parse_month(raw,month)
            except ResearchStopped: raise
            except Exception as e: raise ValueError(f'{name}: {e}. I file validi già scaricati restano salvati.') from e
            if not path.exists():
                tmp=path.with_suffix('.tmp');tmp.write_bytes(raw);os.replace(tmp,path)
            check.write_text(expected+'  '+name)
            rows.extend(parsed);manifest.append({'file':name,'sha256':expected,'source':url})
            done+=1;progress(done,len(symbols)*len(months),f'{symbol} · {month}')
        validate(rows);datasets.append({'pair':symbol,'symbol':symbol.replace('USDT','/USDT'),'rows':rows})
    return datasets,manifest
