import os,json,time,concurrent.futures
from pathlib import Path
os.environ['EASTMONEY_NET_PATCH']='0';os.environ['EASTMONEY_VERIFY_TLS']='1'
from stock_analysis_mcp.data.providers import tencent
root=Path(os.environ.get('SECTOR_V1_ROOT',str(Path(__file__).parent)))/'data'; universe=json.loads((root/'stock_universe.json').read_text())
def one(c):
 p=root/'stocks'/f'{c}.json';x=json.loads(p.read_text()) if p.exists() else {'symbol':c,'name':universe[c]}
 if len(x.get('raw',[]))>=300:return c,len(x['raw'])
 rows=tencent.fetch_stock_kline(c,limit=640,adjust='',start_date='20230101',end_date='20261103')
 if rows:x['raw']=rows;x['raw_fetched_at']=time.strftime('%Y-%m-%dT%H:%M:%SZ',time.gmtime());p.write_text(json.dumps(x,ensure_ascii=False))
 print(c,len(rows),flush=True);return c,len(rows)
with concurrent.futures.ThreadPoolExecutor(max_workers=3) as ex:r=list(ex.map(one,sorted(universe)))
(root/'raw_download_summary.json').write_text(json.dumps({'stocks':len(r),'usable':sum(n>=300 for c,n in r),'failed':[c for c,n in r if n<300]},ensure_ascii=False,indent=2))
print('DONE',sum(n>=300 for c,n in r),'/',len(r),flush=True)
