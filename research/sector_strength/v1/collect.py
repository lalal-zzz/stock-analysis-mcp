import os,json,time,concurrent.futures,traceback
from pathlib import Path
os.environ['EASTMONEY_NET_PATCH']='0';os.environ['EASTMONEY_VERIFY_TLS']='1'
os.environ['STOCK_ANALYSIS_DATA_DIR']=str(Path(os.environ.get('SECTOR_V1_ROOT',str(Path(__file__).parent)))/'data'/'local')
from stock_analysis_mcp.data.providers import sina,tencent
root=Path(os.environ.get('SECTOR_V1_ROOT',str(Path(__file__).parent)))/'data'; (root/'members').mkdir(exist_ok=True);(root/'stocks').mkdir(exist_ok=True)
def save(p,x):p.write_text(json.dumps(x,ensure_ascii=False))
# Use already fetched response, preserving provider's declared member count.
raw=json.loads((root/'source_sina_nodes.json').read_text()); nodes=json.loads(raw[raw.index('{'):raw.rindex('}')+1])
sectors=[]
for node,text in sorted(nodes.items()):
 a=text.split(','); expected=int(a[2]);
 # bounded pilot: complete small/medium industries, no per-industry stock truncation
 if 8<=expected<=80:sectors.append({'sector_code':node,'sector_name':a[1],'expected_count':expected,'sector_type':'sina_industry','membership_asof':'2026-11-03'})
print('eligible_industries',len(sectors),'declared_stocks',sum(s['expected_count'] for s in sectors),flush=True)
def members(s):
 p=root/'members'/f"{s['sector_code']}.json"
 try:
  rows=json.loads(p.read_text()) if p.exists() else sina.fetch_sector_members(s['sector_code'],sector_code=s['sector_code'])
  save(p,rows);s['actual_count']=len(rows);s['complete']=len(rows)==s['expected_count']
  print('members',s['sector_name'],len(rows),s['expected_count'],flush=True)
 except Exception as e:s['actual_count']=0;s['complete']=False;s['error']=str(e)
 return s
with concurrent.futures.ThreadPoolExecutor(max_workers=3) as ex:sectors=list(ex.map(members,sectors))
save(root/'pilot_sectors.json',sectors)
stocks={}
for s in sectors:
 if not s['complete']:continue
 for r in json.loads((root/'members'/f"{s['sector_code']}.json").read_text()):stocks[r['stock_code']]=r['stock_name']
# Exclude current risk-warning stocks, record exclusion (historical status still unknown).
excluded={c:n for c,n in stocks.items() if 'ST' in n.upper() or '退' in n}; stocks={c:n for c,n in stocks.items() if c not in excluded}
save(root/'excluded_current_risk.json',excluded);save(root/'stock_universe.json',stocks)
print('download_stocks',len(stocks),'excluded',len(excluded),flush=True)
def stock(c):
 p=root/'stocks'/f'{c}.json'
 if p.exists():
  old=json.loads(p.read_text())
  if len(old.get('qfq',[]))>=300 and len(old.get('raw',[]))>=300:return c,True
 result={'symbol':c,'name':stocks[c],'fetched_at':time.strftime('%Y-%m-%dT%H:%M:%SZ',time.gmtime())}
 try:
  for key,adj in [('qfq','qfq'),('raw','')]:
   result[key]=tencent.fetch_stock_kline(c,limit=640,adjust=adj,start_date='20230101',end_date='20261103')
  save(p,result); ok=len(result['qfq'])>=300 and len(result['raw'])>=300
  print('stock',c,len(result['qfq']),len(result['raw']),flush=True);return c,ok
 except Exception as e:result['error']=str(e);save(p,result);return c,False
with concurrent.futures.ThreadPoolExecutor(max_workers=4) as ex:status=list(ex.map(stock,sorted(stocks)))
save(root/'download_summary.json',{'stocks':len(stocks),'success':sum(ok for _,ok in status),'failed':[c for c,ok in status if not ok],'sectors':len(sectors),'complete_sectors':sum(s['complete'] for s in sectors)})
print('DONE',sum(ok for _,ok in status),'/',len(stocks),flush=True)
