import os,json,time,concurrent.futures,threading
from pathlib import Path
os.environ['EASTMONEY_NET_PATCH']='0';os.environ['EASTMONEY_VERIFY_TLS']='1'
from stock_analysis_mcp.data.network import http_get_text
root=Path(os.environ.get('SECTOR_V1_ROOT',str(Path(__file__).parent)))/'data';universe=json.loads((root/'stock_universe.json').read_text());(root/'factors').mkdir(exist_ok=True)
lock=threading.Lock();last=[0.]
def one(c):
 p=root/'factors'/f'{c}.json'
 if p.exists():return c,bool(json.loads(p.read_text()).get('data'))
 pref=('sh' if c.startswith('6') else 'bj' if c.startswith(('4','8','9')) else 'sz')+c
 with lock:
  delay=.3-(time.monotonic()-last[0])
  if delay>0:time.sleep(delay)
  last[0]=time.monotonic()
 text=http_get_text(f'https://finance.sina.com.cn/realstock/company/{pref}/qfq.js',retries=1,timeout=12,use_cookies=False,extra_headers={'Referer':'https://finance.sina.com.cn'})
 try:
  x=json.loads(text[text.index('{'):text.rindex('}')+1])
  if not x.get('data'):raise ValueError('no factors')
  x['source']='sina_qfq_factor';x['fetched_at']=time.strftime('%Y-%m-%dT%H:%M:%SZ',time.gmtime());p.write_text(json.dumps(x,ensure_ascii=False));print(c,'factors',len(x['data']),flush=True);return c,True
 except Exception as e:p.write_text(json.dumps({'error':str(e)}));print(c,'FAILED',flush=True);return c,False
with concurrent.futures.ThreadPoolExecutor(max_workers=4) as ex:r=list(ex.map(one,sorted(universe)))
(root/'factor_download_summary.json').write_text(json.dumps({'stocks':len(r),'success':sum(ok for c,ok in r),'failed':[c for c,ok in r if not ok]},indent=2))
print('DONE',sum(ok for c,ok in r),'/',len(r),flush=True)
