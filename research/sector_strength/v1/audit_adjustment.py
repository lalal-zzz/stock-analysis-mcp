import os,importlib.util,json
from pathlib import Path
import pandas as pd,numpy as np
root=Path(__file__).parent;spec=importlib.util.spec_from_file_location('analysis',root/'analyze.py');a=importlib.util.module_from_spec(spec);spec.loader.exec_module(a)
candidates=[]
for p in sorted((root/'data'/'stocks').glob('*.json')):
 x=json.loads(p.read_text());sp=root/'data'/'qfq'/p.name;native=json.loads(sp.read_text()) if sp.exists() else x.get('qfq',[]);fp=root/'data'/'factors'/p.name;fac=json.loads(fp.read_text()).get('data',[]) if fp.exists() else []
 if len(native)>300 and len(x.get('raw',[]))>300 and fac and native[-1]['date']=='2026-11-03':candidates.append((p.stem,x,native,fac))
rows=[]
for idx in np.linspace(0,len(candidates)-1,min(20,len(candidates)),dtype=int):
 c,x,native,fac=candidates[idx];adj,_=a.adjust_ohlc(pd.DataFrame(x['raw']),fac,'2026-11-03')
 n=pd.DataFrame(native).set_index('date').close.pct_change(fill_method=None);s=adj.set_index('date').close.pct_change(fill_method=None)
 pair=pd.concat([s.rename('factor_return'),n.rename('native_return')],axis=1).dropna();diff=(pair.factor_return-pair.native_return).abs()
 rows.append({'symbol':c,'observations':len(pair),'return_corr':pair.factor_return.corr(pair.native_return),'mean_abs_daily_return_difference':diff.mean(),'p95_abs_daily_return_difference':diff.quantile(.95),'max_abs_daily_return_difference':diff.max()})
pd.DataFrame(rows).to_csv(root/'results'/'adjustment_crosscheck.csv',index=False)
print(pd.DataFrame(rows).describe().to_string())
