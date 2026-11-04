import os,json,time,concurrent.futures
from pathlib import Path
os.environ['EASTMONEY_NET_PATCH']='0';os.environ['EASTMONEY_VERIFY_TLS']='1'
from stock_analysis_mcp.data.providers import tencent
root=Path(os.environ.get('SECTOR_V1_ROOT',str(Path(__file__).parent)))/'data'; universe=json.loads((root/'stock_universe.json').read_text())
def one(c):
 p=root/'qfq'/f'{c}.json'
 oldpath=root/'stocks'/f'{c}.json'; old=json.loads(oldpath.read_text())
 if len(old.get('qfq',[]))>=300:return c,len(old['qfq'])
 if p.exists() and len(json.loads(p.read_text()))>=300:return c,len(json.loads(p.read_text()))
 # Same Tencent qfq provider, alternative official proxy endpoint. No date range requests for a bounded latest-640 window.
 rows=tencent.fetch_stock_kline(c,limit=640,adjust='qfq')
 if rows:p.write_text(json.dumps(rows,ensure_ascii=False))
 print(c,len(rows),flush=True);return c,len(rows)
(root/'qfq').mkdir(exist_ok=True)
with concurrent.futures.ThreadPoolExecutor(max_workers=3) as ex:r=list(ex.map(one,sorted(universe)))
(root/'qfq_download_summary.json').write_text(json.dumps({'stocks':len(r),'usable':sum(n>=300 for c,n in r),'failed':[c for c,n in r if n<300]},ensure_ascii=False,indent=2))
print('DONE',sum(n>=300 for c,n in r),'/',len(r),flush=True)
