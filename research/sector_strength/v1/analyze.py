"""Exploratory current-constituent sector scoring; no production rule activation."""
import os, json, math, hashlib, subprocess, warnings
from pathlib import Path
import numpy as np
import pandas as pd
warnings.filterwarnings('ignore', category=RuntimeWarning)
ROOT=Path(os.environ.get('SECTOR_V1_ROOT',str(Path(__file__).parent))); DATA=ROOT/'data'
OUT=ROOT/'results'
OUT.mkdir(parents=True,exist_ok=True)
HORIZONS=(1,3,5,10)

def dump(p,x):p.write_text(json.dumps(x,ensure_ascii=False,indent=2,default=lambda v:float(v) if isinstance(v,np.generic) else str(v)),encoding='utf-8')
def streak(x):
 a=x.fillna(False).astype(bool);return a.groupby((~a).cumsum()).cumsum().astype(float)
def adjust_ohlc(raw,factors,cutoff):
 factors=pd.DataFrame(factors);fs=pd.Series(pd.to_numeric(factors.f,errors='coerce').to_numpy(),index=factors.d.astype(str)).sort_index()
 fs=fs[~fs.index.duplicated(keep='last')];fs=fs[(fs>0)&(fs.index<=cutoff)]
 raw=raw.drop_duplicates('date').set_index('date').sort_index()
 factor=fs.reindex(fs.index.union(raw.index)).sort_index().ffill().reindex(raw.index)
 q=raw.copy()
 for col in ['open','high','low','close']:q[col]=q[col]/factor
 return q.reset_index(),raw.reset_index()

def build_panel():
 sectors=json.loads((DATA/'pilot_sectors.json').read_text()); universe=json.loads((DATA/'stock_universe.json').read_text())
 bm=pd.DataFrame(json.loads((DATA/'benchmark_csi300.json').read_text())).drop_duplicates('date').set_index('date').sort_index()
 dates=bm.index; bmr=bm.close.pct_change(fill_method=None); stock={};quality=[]
 for c,n in universe.items():
  p=DATA/'stocks'/f'{c}.json'
  if not p.exists():quality.append({'symbol':c,'status':'missing'});continue
  box=json.loads(p.read_text());sidecar=DATA/'qfq'/f'{c}.json'; qrows=json.loads(sidecar.read_text()) if sidecar.exists() else box.get('qfq',[]);raw=pd.DataFrame(box.get('raw',[]))
  fp=DATA/'factors'/f'{c}.json'; factors=json.loads(fp.read_text()).get('data',[]) if fp.exists() else []
  if raw.empty or not factors:quality.append({'symbol':c,'status':'missing_raw_or_factor','raw_rows':len(raw)});continue
  q,raw=adjust_ohlc(raw,factors,dates[-1])
  if len(q)<300 or len(raw)<300:quality.append({'symbol':c,'status':'short','qfq_rows':len(q),'raw_rows':len(raw)});continue
  q=q.drop_duplicates('date').set_index('date').sort_index().reindex(dates); raw=raw.drop_duplicates('date').set_index('date').sort_index().reindex(dates)
  valid=(q.close>0)&(q.open>0)&(q.high>=q[['open','close','low']].max(axis=1))&(q.low<=q[['open','close','high']].min(axis=1))&(raw.close>0)
  q.loc[~valid,['close','open']]=np.nan
  if q.close.notna().sum()<300 or q.close.last_valid_index()<dates[-5]:
   quality.append({'symbol':c,'name':n,'status':'stale_or_insufficient_overlap','overlap_rows':int(q.close.notna().sum()),'last':q.close.last_valid_index()});continue
  ret=q.close.pct_change(fill_method=None);prev=raw.close.shift(1)
  # Candidate ONLY: standard board limit, no point-in-time ST/IPO/reference-price verification.
  rate=.2 if c.startswith(('30','68')) else (.3 if c.startswith(('4','8','9')) else .1)
  limitprice=np.floor(prev*(1+rate)*100+0.5000001)/100
  consistent=(ret-raw.close.pct_change(fill_method=None)).abs()<.002
  candidate=(raw.close-limitprice).abs()<.001
  candidate=candidate&consistent&(raw.volume>0)&(q.close.notna().cumsum()>20)
  d=pd.DataFrame({'ret':ret,'up':(ret>0).where(ret.notna()),'above20':(q.close>q.close.rolling(20,min_periods=20).mean()).where(q.close.rolling(20).mean().notna()),'candidate':candidate.where(ret.notna()),'volratio':raw.volume/raw.volume.shift(1).rolling(5,min_periods=5).mean(),'streak':streak(candidate)},index=dates)
  for h in HORIZONS:d[f'fwd{h}']=q.open.shift(-(h+1))/q.open.shift(-1)-1
  stock[c]=d
  quality.append({'symbol':c,'name':n,'status':'ok','qfq_rows':int(q.close.notna().sum()),'raw_rows':int(raw.close.notna().sum()),'first':q.close.first_valid_index(),'last':q.close.last_valid_index()})
 pd.DataFrame(quality).to_csv(OUT/'stock_coverage.csv',index=False)
 frames=[];coverage=[]
 for s in sectors:
  code=s['sector_code']; members=json.loads((DATA/'members'/f'{code}.json').read_text()) if (DATA/'members'/f'{code}.json').exists() else []
  eligible=[r['stock_code'] for r in members if r['stock_code'] in universe]; available=[c for c in eligible if c in stock]
  fraction=len(available)/len(eligible) if eligible else 0
  coverage.append({**s,'eligible_members':len(eligible),'usable_stocks':len(available),'download_coverage':fraction})
  if not s['complete'] or fraction<.95 or len(available)<8:continue
  fields=['ret','up','above20','candidate','volratio','streak']+[f'fwd{h}' for h in HORIZONS]
  matrices={k:pd.concat({c:stock[c][k] for c in available},axis=1).astype(float) for k in fields}
  frac=matrices['ret'].notna().sum(axis=1)/len(eligible); ok=frac>=.95
  d=pd.DataFrame(index=dates)
  d['ret']=matrices['ret'].mean(axis=1).where(ok);d['excess']=d.ret-bmr
  d['breadth']=matrices['up'].mean(axis=1).where(ok)
  d['above20']=matrices['above20'].mean(axis=1).where(matrices['above20'].notna().sum(axis=1)/len(eligible)>=.95)
  d['L']=matrices['candidate'].sum(axis=1).where(ok)
  d['N']=len(eligible);d['coverage']=frac
  d['volratio']=matrices['volratio'].clip(.05,10).mean(axis=1).where(ok)
  d['max_streak']=matrices['streak'].max(axis=1).where(ok)
  d['promotion']=((matrices['candidate']==1)&(matrices['candidate'].shift(1)==1)).sum(axis=1)/matrices['candidate'].shift(1).sum(axis=1).replace(0,np.nan)
  d['candidate_next_ret']=matrices['ret'].where(matrices['candidate'].shift(1)==1).mean(axis=1)
  for h in HORIZONS:
   count=matrices[f'fwd{h}'].notna().sum(axis=1)/len(eligible)
   d[f'y{h}']=(matrices[f'fwd{h}'].mean(axis=1)-(bm.open.shift(-(h+1))/bm.open.shift(-1)-1)).where(count>=.95)
  d['sector_code']=code;d['sector_name']=s['sector_name'];d['date']=d.index
  frames.append(d.reset_index(drop=True))
 pd.DataFrame(coverage).to_csv(OUT/'sector_coverage.csv',index=False)
 if len(frames)<8:raise RuntimeError(f'Only {len(frames)} sufficiently covered industries; cannot run cross-sectional optimization')
 panel=pd.concat(frames,ignore_index=True).sort_values(['sector_code','date'])
 market=pd.concat({c:d['candidate'] for c,d in stock.items()},axis=1).astype(float).mean(axis=1).rename('market_p');market.index.name='date'
 panel=panel.merge(market,on='date',how='left')
 panel['excess_rank']=panel.groupby('date').excess.rank(pct=True)
 return panel,quality,coverage

def features(panel,half=3,power=1,linear=False):
 out=[]
 for _,g in panel.groupby('sector_code',sort=False):
  g=g.sort_values('date').copy();e=g.excess
  sigma=e.shift(1).rolling(60,min_periods=40).std().clip(lower=.003)
  z=.4*e/sigma+.35*e.rolling(3,min_periods=3).sum()/(sigma*np.sqrt(3))+.25*e.rolling(10,min_periods=10).sum()/(sigma*np.sqrt(10))
  m=(.5+z/6).clip(0,1) if linear else .5+.5*np.tanh(z/2)
  p=(g.L+20*g.market_p)/(g.N+20)
  if linear:u=(g.L/(g.N*.1)).clip(0,1)
  else:
   u=.4*np.log1p(g.L)/np.log1p(g.N)+.6*np.sqrt(p.clip(0,1))
   u=1-np.exp(-u/.2)
  # Missing observations remain unavailable; decayed history cannot fill today's gap.
  h=u.ewm(halflife=half,adjust=False,min_periods=10).mean().where(g.ret.notna())
  strong=(e>0)&(g.excess_rank>=.7)&g.ret.notna()
  q=strong.astype(float).ewm(halflife=half,adjust=False,min_periods=10).mean()
  days=streak(strong)
  c=(q**power)*(np.minimum(days/5,1) if linear else 1-np.exp(-days/3))
  b=.5*g.breadth+.5*g.above20
  f=((g.volratio-1)/2+.5).clip(0,1) if linear else .5+.5*np.tanh(np.log(g.volratio.clip(lower=.01))/np.log(2))
  accel=e.ewm(span=3,adjust=False,min_periods=3).mean()-e.ewm(span=10,adjust=False,min_periods=10).mean()
  a=np.tanh(accel/sigma)
  decay=(h.shift(1)-h).clip(lower=0)+(b.shift(1)-b).clip(lower=0)
  for k,v in {'M':m,'H':h,'C':c,'B':b,'F':f,'A':a,'D':decay}.items():g[k]=v
  g['eligible_score']=g[['M','H','C','B','F','A','D']].notna().all(axis=1)
  out.append(g)
 return pd.concat(out).sort_values(['date','sector_code']).reset_index(drop=True)

WEIGHTS={'balanced':(.25,.25,.20,.15,.15),'momentum':(.40,.15,.15,.20,.10),'limit':(.20,.40,.20,.10,.10),'persistence':(.20,.20,.35,.15,.10)}
def score(f,weights,interaction=True):
 s=sum(f[k]*w for k,w in zip(['M','H','C','B','F'],weights))
 if interaction:s=s+.10*np.sqrt(f.M*f.H)*f.C*f.B+.05*f.A-.15*f.D
 return (100*s.clip(0,1)).where(f.eligible_score)

def daily_metrics(f,col,mask,h=5):
 rows=[]
 for date,g in f.loc[mask,['date','sector_code',col,f'y{h}']].dropna().groupby('date'):
  if len(g)<8:continue
  g=g.sort_values([col,'sector_code']);k=max(2,int(np.ceil(len(g)*.2)))
  ic=g[col].rank().corr(g[f'y{h}'].rank()) if g[col].nunique()>1 and g[f'y{h}'].nunique()>1 else np.nan
  rows.append({'date':date,'n':len(g),'ic':ic,'top_excess':g.iloc[-k:][f'y{h}'].mean(),'bottom_excess':g.iloc[:k][f'y{h}'].mean(),'spread':g.iloc[-k:][f'y{h}'].mean()-g.iloc[:k][f'y{h}'].mean(),'universe_excess':g[f'y{h}'].mean()})
 return pd.DataFrame(rows)
def summarize(d):
 if d.empty:return {'days':0}
 return {'days':len(d),'mean_ic':float(d.ic.mean()),'mean_top_excess':float(d.top_excess.mean()),'mean_spread':float(d.spread.mean()),'mean_universe_excess':float(d.universe_excess.mean()),'top_beats_benchmark_rate':float((d.top_excess>0).mean())}
def block_ci(x,block=10,n=1000):
 x=np.array(x,dtype=float);x=x[np.isfinite(x)]
 if len(x)<block*2:return [None,None]
 rng=np.random.default_rng(20261103);vals=[]
 for _ in range(n):
  starts=rng.integers(0,len(x),size=math.ceil(len(x)/block));idx=np.concatenate([(s+np.arange(block))%len(x) for s in starts])[:len(x)];vals.append(x[idx].mean())
 return np.quantile(vals,[.025,.975]).tolist()

def run():
 panel,quality,coverage=build_panel();panel.to_parquet(OUT/'industry_daily_panel.parquet',index=False)
 base=features(panel);dates=sorted(base.loc[base[['M','H','C','B','F','A','D']].notna().all(axis=1)].date.unique())
 n=len(dates); cut1=int(n*.6);cut2=int(n*.8);purge=11
 if n<200:raise RuntimeError(f'Only {n} usable dates')
 splits={'train':dates[:cut1-purge],'validation':dates[cut1:cut2-purge],'test':dates[cut2:]}
 dump(OUT/'split.json',{k:{'start':v[0],'end':v[-1],'signal_dates':len(v)} for k,v in splits.items()})
 configs=[{'name':'linear_baseline','half':3,'power':1,'weights':'balanced','linear':True,'interaction':False},{'name':'nonlinear_fixed','half':3,'power':1,'weights':'balanced','linear':False,'interaction':True}]
 for half in [1,3,5,10]:
  for power in [1,2]:
   for w in WEIGHTS:configs.append({'name':f'h{half}_p{power}_{w}','half':half,'power':power,'weights':w,'linear':False,'interaction':True})
 fcache={}; scores={};records=[]; daily={}
 for cfg in configs:
  key=(cfg['half'],cfg['power'],cfg['linear'])
  if key not in fcache:fcache[key]=features(panel,*key)
  f=fcache[key];v=score(f,WEIGHTS[cfg['weights']],cfg['interaction']);scores[cfg['name']]=v;f=f.assign(score=v)
  if 'linear_baseline' in scores:assert v.notna().equals(scores['linear_baseline'].notna()), 'All models must use identical sector/date observations'
  rec=dict(cfg)
  for split,ds in splits.items():
   dm=daily_metrics(f,'score',f.date.isin(ds));daily[(cfg['name'],split)]=dm
   rec.update({f'{split}_{k}':v for k,v in summarize(dm).items()})
  records.append(rec)
 results=pd.DataFrame(records)
 # Validation only. Test values are never used to choose configuration.
 selected=results[~results.name.isin(['linear_baseline','nonlinear_fixed'])].sort_values(['validation_mean_ic','name'],ascending=[False,True]).iloc[0]['name']
 results['selected']=results.name==selected;results.to_csv(OUT/'model_comparison.csv',index=False)
 chosen=next(c for c in configs if c['name']==selected);f=fcache[(chosen['half'],chosen['power'],chosen['linear'])].copy();f['score']=scores[selected]
 baseline=fcache[(3,1,True)].copy();baseline['score']=scores['linear_baseline']
 # All hypotheses evaluated on same covered sector/day records; transformations do not change availability.
 assert f[['date','sector_code']].equals(baseline[['date','sector_code']])
 checks=[];summary={}
 for model in ['linear_baseline','nonlinear_fixed',selected]:
  dm=daily[(model,'test')];dm.to_csv(OUT/f'test_daily_{model}.csv',index=False)
  summary[model]=summarize(dm);summary[model]['ic_95_block_ci']=block_ci(dm.ic);summary[model]['spread_95_block_ci']=block_ci(dm.spread)
 # Paired model differences, not merely separate intervals.
 pair=daily[(selected,'test')].merge(daily[('linear_baseline','test')],on='date',suffixes=('_selected','_baseline'))
 paired={'mean_ic_delta':float((pair.ic_selected-pair.ic_baseline).mean()),'ic_delta_95_block_ci':block_ci(pair.ic_selected-pair.ic_baseline),'mean_spread_delta':float((pair.spread_selected-pair.spread_baseline).mean()),'spread_delta_95_block_ci':block_ci(pair.spread_selected-pair.spread_baseline)}
 horizons=[]
 for h in HORIZONS:
  for name,fr in [('linear_baseline',baseline),(selected,f)]:horizons.append({'model':name,'horizon':h,**summarize(daily_metrics(fr,'score',fr.date.isin(splits['test']),h))})
 pd.DataFrame(horizons).to_csv(OUT/'test_horizons.csv',index=False)
 qs=[]
 for date,g in f[f.date.isin(splits['test'])].dropna(subset=['score','y5']).groupby('date'):
  if len(g)<10:continue
  g=g.sort_values(['score','sector_code']).copy();g['quintile']=np.minimum(np.arange(len(g))*5//len(g)+1,5)
  for q,gg in g.groupby('quintile'):qs.append({'date':date,'quintile':q,'excess_5d':gg.y5.mean()})
 qdf=pd.DataFrame(qs).groupby('quintile').excess_5d.agg(['mean','count']).reset_index();qdf.to_csv(OUT/'test_quintiles.csv',index=False)
 ablations=[]
 for remove in ['M','H','C','B','F','A_D']:
  g=f.copy();ws=np.array(WEIGHTS[chosen['weights']])
  if remove in ['M','H','C','B','F']:
   i=['M','H','C','B','F'].index(remove);ws[i]=0;ws/=ws.sum();g[remove]=0
  else:g['A']=0;g['D']=0
  g['score']=score(g,ws,True)
  assert g.score.notna().equals(f.score.notna()), 'Ablations must retain original availability mask'
  ablations.append({'removed':remove,**summarize(daily_metrics(g,'score',g.date.isin(splits['test'])))})
 pd.DataFrame(ablations).to_csv(OUT/'test_ablations_exploratory.csv',index=False)
 latest_date=max(f.date);latest=f[f.date==latest_date].dropna(subset=['score']).sort_values('score',ascending=False).copy()
 latest['rank']=np.arange(1,len(latest)+1);latest.to_csv(OUT/'latest_scores.csv',index=False)
 f.to_parquet(OUT/'scores_history.parquet',index=False)
 # Causality: recomputing on a prefix must leave past features unchanged.
 prefix_date=dates[cut1];prefix=features(panel[panel.date<=prefix_date],chosen['half'],chosen['power'],chosen['linear'])
 past=f[f.date<=prefix_date].reset_index(drop=True)
 assert prefix[['date','sector_code']].equals(past[['date','sector_code']])
 for k in ['M','H','C','B','F','A','D']:np.testing.assert_allclose(prefix[k],past[k],equal_nan=True,rtol=1e-10)
 for split in ['train','validation']:
  end_i=dates.index(splits[split][-1]); nextsplit='validation' if split=='train' else 'test';assert end_i+11<dates.index(splits[nextsplit][0])
 checks=['prefix invariance: passed','11-session split embargo: passed','model record alignment and identical eligible-score masks: passed','selection uses validation IC only: passed']
 dump(OUT/'verification.json',checks)
 manifest={'repo':'https://github.com/lalal-zzz/stock-analysis-mcp','commit':subprocess.check_output(['git','-C',str(Path(__file__).resolve().parents[3]),'rev-parse','HEAD'],text=True).strip(),'data_source':'repository Sina industry members, Tencent raw stock bars, Sina qfq factors (same factor source as repository akshare/Sina fallback); adjusted OHLC=raw/factor for every stock, no per-stock price-source splicing; CSI300 raw index bars','selection_universe':'Sina declared member counts 8..80, complete membership response, >=95% download/day coverage, >=8 usable members','asof':latest_date,'quality_ok_stocks':sum(q['status']=='ok' for q in quality),'requested_stocks':len(quality),'industries':int(panel.sector_code.nunique()),'panel_rows':len(panel),'raw_date_start':panel.date.min(),'raw_date_end':panel.date.max(),'selected_config':chosen,'test_summary':summary,'paired_delta':paired,'price_basis':'all stock prices use Tencent raw divided by Sina qfq factors; downloaded Tencent qfq is retained as auxiliary evidence, not mixed into analysis', 'label':'next-open to open h sessions later, stock-level forward returns averaged by current industry, minus CSI300 same interval; event study, no executable portfolio or costs','limitations':['Current membership/size filter and exclusion of current ST stocks create selection/survivorship bias.','Candidate limit-up is inferred from standard board raw previous close, rounded to cents, qfq/raw return consistency, >20 observed sessions; not verified exchange limit prices; historical ST/IPO status unavailable.','Industry basket is equal-weight current constituents, not official Eastmoney sector index.','Money-flow history unavailable; volume ratio used instead; no sector-code cross-provider merging.','No fees/slippage/limit-up fills/portfolio capacity model.','34 parameter configurations; selection on validation only; test is one exploratory holdout.','A stock may occur in multiple current industry definitions; industry observations not independent.']}
 dump(OUT/'results.json',manifest)
 # Deterministic manifest of every raw input; archive packages data as well.
 paths=sorted(DATA.rglob('*.json'));dump(OUT/'raw_data_manifest.json',[{'path':str(p.relative_to(DATA)),'bytes':p.stat().st_size,'sha256':hashlib.sha256(p.read_bytes()).hexdigest()} for p in paths])
 make_report(manifest,results,qdf,latest,checks)
 print(json.dumps({'selected':selected,'summary':summary,'paired':paired,'industries':manifest['industries'],'stocks':manifest['quality_ok_stocks']},ensure_ascii=False,indent=2))

def make_report(m,results,qdf,latest,checks):
 import os
 os.environ.setdefault('MPLCONFIGDIR',str(ROOT/'plot-cache'))
 import matplotlib
 matplotlib.use('Agg')
 import matplotlib.pyplot as plt
 fig,ax=plt.subplots(figsize=(7,4));ax.bar(qdf.quintile,qdf['mean']*100,color='#4263eb');ax.axhline(0,color='black',lw=.8);ax.set(xlabel='Score quintile (1=lowest, 5=highest)',ylabel='Mean 5-session excess return (%)',title='Untouched test period · next-open event study');fig.tight_layout();fig.savefig(OUT/'test_quintiles.png',dpi=160);plt.close(fig)
 chosen=m['selected_config'];spl=json.loads((OUT/'split.json').read_text());rows=[]
 for name,s in m['test_summary'].items():rows.append(f"| {name} | {s.get('days',0)} | {s.get('mean_ic',float('nan')):.4f} | {s.get('mean_top_excess',float('nan'))*100:.3f}% | {s.get('mean_spread',float('nan'))*100:.3f}% |")
 top=[]
 for _,r in latest.head(10).iterrows():top.append(f"| {r['sector_name']} | {r['score']:.2f} | {r['ret']*100:.2f}% | {int(r['L'])} | {r['breadth']*100:.1f}% | {r['coverage']*100:.1f}% |")
 text=f'''# 强势板块评分：首轮探索实验

数据截止：{m['asof']}。本报告使用真实下载数据，但受数据源可用性及历史成分股缺失限制，属于探索研究，不构成全市场正式验证。

## 数据与范围

- 仓库版本：`{m['commit']}`。
- 下载股票：{m['requested_stocks']}；通过至少300条窗口重合、价格有效性和近期交易日期检查：{m['quality_ok_stocks']}。
- 进入分析的行业：{m['industries']}；行业日记录：{m['panel_rows']}。
- 日线范围：{m['raw_date_start']} 至 {m['raw_date_end']}；腾讯未复权日线请求640条，调整价格统一使用新浪因子。
- 东方财富3个目录地址、3个历史地址均未获得可用响应。改用仓库已有新浪行业成分股和腾讯个股数据；腾讯原复权地址部分请求HTTP 501，官方代理地址也仅覆盖部分股票。为统一分析口径，全部股票用腾讯未复权OHLC除以新浪前复权因子构造调整价格（该因子源也是仓库akshare/新浪降级链的来源）。已下载的腾讯前复权记录保留，但不混入分析价格；**没有把新浪行业代码伪装成东方财富BK代码**。
- 先按当前新浪行业声明股票数8～80确定小中型行业试验集，再要求成分响应完整、下载及每日覆盖至少95%；没有按回测收益挑选行业。该规模筛选仍会造成选择偏差。
- 基准：沪深300。同一行业当前成分股等权重建；不是官方板块指数。资金流历史不可用，量能用股票成交量比替代。

## 模型与优化规则

对比线性基线、固定非线性模型，以及32个非线性候选（半衰期1/3/5/10日 × 连续性幂次1/2 × 四组权重），共34条配置记录，其中固定非线性与网格的一项相同，实际33种不同配置。

- 动量：1/3/10日相对收益按过去60日波动率标准化，非线性版用tanh限幅；波动率窗口滞后一天。
- 涨停候选强度：`u=1-exp(-[0.4*log(1+L)/log(1+N)+0.6*sqrt((L+20*p_market)/(N+20))]/0.2)`，再用指定半衰期EWMA衰减。`p_market`指本次可用股票去重后的涨停候选率，并非全市场真实涨停率。
- 连续性：当天相对收益为正且进入试验行业前30%记为强势；`C=EWMA(强势)^power * (1-exp(-连续强势天数/3))`。
- 广度：上涨比例与高于MA20比例各半。量能：`F=0.5+0.5*tanh(log(成交量比)/log(2))`。
- 加速度：EMA3与EMA10相对收益之差，用历史波动率标准化并tanh限幅。退潮：涨停强度与广度的单日下降量。
- 非线性总分：`100*clip(w·[M,H,C,B,F]+0.10*sqrt(M*H)*C*B+0.05*A-0.15*D,0,1)`。
- 线性基线用线性限幅映射、线性涨停比例及连续天数饱和，无交互和加速/退潮项；详细代码随附。
- 按日期60%/20%/20%切分，两处分界前各剔除11个信号交易日，避免最长10日标签跨越分界。
- 只按验证集平均5日Rank IC选参数；测试集不参与选参。选中：`{chosen['name']}`，权重 `{WEIGHTS[chosen['weights']]}`，半衰期{chosen['half']}日、连续性幂次{chosen['power']}。

### 时间切分

| 区间 | 起日 | 止日 | 信号日期数 |
|---|---|---|---:|
'''+''.join(f"| {k} | {v['start']} | {v['end']} | {v['signal_dates']} |\n" for k,v in spl.items())+'''
## 独立测试期结果

全部模型及分项移除诊断均强制使用完全相同的行业/日期可用性掩码，避免因缺失字段导致比较样本不同。信号使用t日收盘信息；标签从t+1日开盘至t+h+1日开盘，减去同期沪深300收益。以下是重叠事件均值，**不是年化收益，也不是可执行组合收益**。Top组为当日得分前20%，Bottom组为后20%。

| 模型 | 有效日期 | 5日平均Rank IC | Top组5日超额 | Top−Bottom |
|---|---:|---:|---:|---:|
'''+ '\n'.join(rows)+f'''

选中模型相对线性基线的平均IC差：{m['paired_delta']['mean_ic_delta']:.4f}；10交易日分块bootstrap的95%区间：{m['paired_delta']['ic_delta_95_block_ci']}。

区间覆盖零时，不能认定复杂模型优于基线。即使本次区间不覆盖零，也仍需历史成分股、不同市场阶段与后续留出数据复验。分块bootstrap只缓解时间重叠，不消除成分选择偏差或模型搜索风险。

![测试期分组收益](test_quintiles.png)

另附1/3/5/10日结果、各分项移除实验。移除实验是测试期事后诊断，不能据此重新挑选“最优参数”。

## 最近日期评分（探索模型）

分数是模型强度量表，不是上涨概率；当前榜单仍有历史成员与涨停识别限制。

| 行业 | 得分 | 当日涨幅 | 涨停候选数 | 上涨占比 | 每日覆盖 |
|---|---:|---:|---:|---:|---:|
'''+ '\n'.join(top)+'''

## 必须保留的局限

1. 使用当前成分股回看历史，且按当前板块规模筛选、排除了当前ST股票，存在选择与幸存者偏差；无法据此声称全市场有效。
2. “涨停候选”按普通市场涨跌停比例、未复权前收盘四舍五入到分、复权/未复权收益一致性、至少20条已观察记录推算。缺少历史风险警示、上市阶段和交易所当日参考价，**不能当作已核实涨停统计**；涉及除权或特殊状态时可能漏判/误判。
3. 腾讯未复权价格/新浪因子的调整收益用于趋势与事件标签，未复权价格用于涨停候选；这是显式记录的跨源因子处理，并非腾讯原生前复权口径。复权因子的准确性与跨源一致性仍需复验，也不自动解决历史成分股问题。
4. 行业篮子非官方指数；资金流、封单、炸板和真实晋级率未纳入验证模型。下载了晋级与连板候选字段，未把它们全部作为可调因子，以控制首次搜索范围。
5. 未模拟手续费、滑点、涨停无法成交、退市收益、仓位容量和组合换手；行业标签可能重叠。
6. 只有一次探索留出测试，历史窗口有限；参数不自动写入生产规则。

## 可复现文件

- `model_comparison.csv`：34个配置的训练/验证/测试统计。
- `test_horizons.csv`、`test_quintiles.csv`：不同持有期与分组结果。
- `test_ablations_exploratory.csv`：事后分项移除诊断。
- `stock_coverage.csv`、`sector_coverage.csv`：股票、行业覆盖。
- `industry_daily_panel.parquet`、`scores_history.parquet`、`latest_scores.csv`：输入面板与评分。
- `results.json`、`split.json`、`verification.json`：结构化结果与检查。
- `raw_data_manifest.json`：原始输入SHA256清单。原始数据和脚本另打包。

已通过检查：'''+ '；'.join(checks)+'。\n'
 (OUT/'report.md').write_text(text,encoding='utf-8')
 # standalone HTML with embedded chart and escaped Markdown source; no remote dependencies
 import html,base64
 img=base64.b64encode((OUT/'test_quintiles.png').read_bytes()).decode()
 htmltext='<!doctype html><meta charset="utf-8"><title>强势板块评分探索报告</title><style>body{max-width:1100px;margin:35px auto;padding:20px;font:16px/1.7 system-ui;background:#f5f7fb;color:#17233c}pre{white-space:pre-wrap;word-break:break-word;background:white;padding:24px;border-radius:12px}img{max-width:100%}</style><h1>强势板块评分探索报告</h1><img src="data:image/png;base64,'+img+'"><pre>'+html.escape(text)+'</pre>'
 (OUT/'report.html').write_text(htmltext,encoding='utf-8')
if __name__=='__main__':run()
