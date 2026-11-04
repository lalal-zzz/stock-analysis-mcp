import os,json,re,time,threading,concurrent.futures,hashlib
from pathlib import Path
os.environ['EASTMONEY_NET_PATCH']='0';os.environ['EASTMONEY_VERIFY_TLS']='1'
import pandas as pd,numpy as np,akshare
from stock_analysis_mcp.data.network import http_get_text
ROOT=Path(os.environ.get('SECTOR_V2_ROOT',str(Path(__file__).parent)));OLD=Path(os.environ.get('SECTOR_V1_ROOT',str(Path(__file__).parent.parent/'v1')));RAW=ROOT/'raw_sina';CACHE=ROOT/'stock_history';RAW.mkdir(exist_ok=True);CACHE.mkdir(exist_ok=True)
panel=pd.read_parquet(OLD/'results'/'industry_daily_panel.parquet');q=pd.read_csv(OLD/'results'/'stock_coverage.csv',dtype={'symbol':str});approved=set(q.loc[q.status=='ok','symbol'])
sectors={};universe={};oldnames=json.loads((OLD/'data'/'stock_universe.json').read_text())
for c in sorted(panel.sector_code.unique()):
 members=json.loads((OLD/'data'/'members'/f'{c}.json').read_text());codes=[m['stock_code'] for m in members if m['stock_code'] in approved]
 sectors[c]={'sector_name':panel.loc[panel.sector_code==c,'sector_name'].iloc[0],'eligible_denominator':int(panel.loc[panel.sector_code==c,'N'].iloc[0]),'stocks':codes}
 for code in codes:universe[code]=oldnames.get(code,code)
(ROOT/'frozen_universe.json').write_text(json.dumps({'sectors':sectors,'stocks':universe},ensure_ascii=False,indent=2))
spec=ROOT/'experiment_spec.json';print('prereg_sha256',hashlib.sha256(spec.read_bytes()).hexdigest(),'stocks',len(universe),'sectors',len(sectors),flush=True)
local=threading.local();rate_lock=threading.Lock();last=[0.]
def decoder():
 if not hasattr(local,'ctx'):
  local.ctx=akshare.stock_zh_a_daily.__globals__['py_mini_racer'].MiniRacer();local.ctx.eval(akshare.stock_zh_a_daily.__globals__['hk_js_decode'])
 return local.ctx

def one(code):
 pref=('sh' if code.startswith('6') else 'sz')+code if not code.startswith(('sh','sz')) else code
 cp=CACHE/f'{pref}.parquet';mp=RAW/f'{pref}.meta.json';textpath=RAW/f'{pref}.js'
 if cp.exists() and mp.exists():return json.loads(mp.read_text())
 url=f'https://finance.sina.com.cn/realstock/company/{pref}/hisdata_klc2/klc_kl.js';started=time.time()
 with rate_lock:
  delay=.35-(time.monotonic()-last[0])
  if delay>0:time.sleep(delay)
  last[0]=time.monotonic()
 text=http_get_text(url,retries=1,timeout=18,use_cookies=False,extra_headers={'Referer':'https://finance.sina.com.cn'})
 meta={'symbol':pref,'url':url,'fetched_at':time.strftime('%Y-%m-%dT%H:%M:%SZ',time.gmtime()),'elapsed_seconds':time.time()-started,'source_market_timestamp_verified':False,'status':'failed'}
 try:
  if not text:raise ValueError('empty response')
  textpath.write_text(text)
  m=re.match(r'\s*var\s+KLC_K2_'+pref+r'\s*=\s*"([^"]+)";?',text)
  if not m:raise ValueError('source symbol header mismatch')
  rows=decoder().call('d',m.group(1));df=pd.DataFrame(rows);df['date']=pd.to_datetime(df.date,utc=True).dt.strftime('%Y-%m-%d')
  df=df.drop_duplicates('date').sort_values('date');meta['source_first_date']=df.date.min();meta['source_last_date']=df.date.max();meta['source_rows']=len(df)
  df=df[df.date<='2026-11-03'].copy()
  for c in ['open','high','low','close','volume','amount']:
   if c in df:df[c]=pd.to_numeric(df[c],errors='coerce')
  if len(df)<300:raise ValueError('insufficient history')
  if (df.date.str.len()!=10).any():raise ValueError('invalid dates')
  df.to_parquet(cp,index=False);meta.update(status='ok',rows=len(df),first_date=df.date.min(),last_date=df.date.max(),payload_sha256=hashlib.sha256(text.encode()).hexdigest(),volume_unit='shares')
  oldpath=OLD/'data'/'stocks'/f'{pref[-6:]}.json'
  if oldpath.exists():
   old=pd.DataFrame(json.loads(oldpath.read_text()).get('raw',[]))
   if not old.empty:
    pair=df[['date','close','open']].merge(old[['date','close','open']],on='date',suffixes=('_sina','_tencent'));meta['tencent_overlap']=len(pair)
    if len(pair):
     meta['close_exact_fraction']=float(((pair.close_sina-pair.close_tencent).abs()<.005).mean());meta['max_close_difference']=float((pair.close_sina-pair.close_tencent).abs().max())
  print(pref,'OK',len(df),df.date.min(),df.date.max(),flush=True)
 except Exception as e:meta['error']=str(e);print(pref,'FAILED',str(e),flush=True)
 mp.write_text(json.dumps(meta,ensure_ascii=False,indent=2));return meta
# benchmark uses exact same price source and known index identity.
benchmark=one('sh000300')
with concurrent.futures.ThreadPoolExecutor(max_workers=4) as ex:results=list(ex.map(one,sorted(universe)))
summary={'requested_stocks':len(universe),'ok':sum(x['status']=='ok' for x in results),'failed':[x['symbol'] for x in results if x['status']!='ok'],'benchmark':benchmark,'results':results}
(ROOT/'download_summary.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2))
print('DONE',summary['ok'],'/',len(universe),flush=True)
