"""Frozen-hypothesis historical validation. Real source data only; no production activation."""
import os,json,math,hashlib,importlib.util,warnings
from pathlib import Path
import numpy as np,pandas as pd
ROOT=Path(os.environ.get('SECTOR_V2_ROOT',str(Path(__file__).parent)));OLD=Path(os.environ.get('SECTOR_V1_ROOT',str(Path(__file__).parent.parent/'v1')));OUT=ROOT/'results';OUT.mkdir(exist_ok=True)
os.environ.setdefault('MPLCONFIGDIR',str(ROOT/'plot-cache'))
spec=importlib.util.spec_from_file_location('v1',Path(__file__).parent.parent/'v1'/'analyze.py');v1=importlib.util.module_from_spec(spec);spec.loader.exec_module(v1)
MODELS=['v1_linear_frozen','v1_selected_frozen','nonmonotonic_heat','walk_forward_ridge']

def dump(p,x):p.write_text(json.dumps(x,ensure_ascii=False,indent=2,default=lambda x:float(x) if isinstance(x,np.generic) else str(x)))
def ordinary_limit_rate(symbol,dates):
 dates=pd.Index(dates).astype(str)
 if symbol.startswith('30'):return pd.Series(np.where(dates<'2020-08-24',.1,.2),index=dates)
 return pd.Series(.2 if symbol.startswith('68') else .3 if symbol.startswith(('4','8','9')) else .1,index=dates)
def forward_open(open_,h):return open_.shift(-(h+1))/open_.shift(-1)-1

def build_panel():
 frozen=json.loads((ROOT/'frozen_universe.json').read_text());bench=pd.read_parquet(ROOT/'stock_history'/'sh000300.parquet').drop_duplicates('date').set_index('date').sort_index()
 bench=bench[bench.index<='2023-12-31'];full_bench=bench.copy();bench=bench[bench.index>='2016-01-01'];dates=bench.index
 stocks={};quality=[];cross=[]
 for code,name in frozen['stocks'].items():
  pref=('sh' if code.startswith('6') else 'sz')+code;p=ROOT/'stock_history'/f'{pref}.parquet';fp=OLD/'data'/'factors'/f'{code}.json'
  if not p.exists() or not fp.exists():quality.append({'symbol':code,'status':'missing'});continue
  factors=json.loads(fp.read_text()).get('data',[])
  if not factors:quality.append({'symbol':code,'status':'missing_factor'});continue
  raw0=pd.read_parquet(p);raw0=raw0[raw0.date<='2023-12-31'];raw0=raw0.sort_values('date')
  q0,raw0=v1.adjust_ohlc(raw0,factors,dates[-1]);raw0=raw0.set_index('date');q0=q0.set_index('date')
  observed=pd.Series(np.arange(1,len(raw0)+1),index=raw0.index).reindex(dates)
  q=q0.reindex(dates);raw=raw0.reindex(dates)
  valid=(q.close>0)&(q.open>0)&(raw.volume>0)&(q.high>=q[['open','close','low']].max(axis=1)-1e-8)&(q.low<=q[['open','close','high']].min(axis=1)+1e-8)
  q.loc[~valid,['open','close']]=np.nan
  if int(q.close.notna().sum())<300:quality.append({'symbol':code,'name':name,'status':'short_historical_overlap','rows':int(q.close.notna().sum())});continue
  ret=q.close.pct_change(fill_method=None);prev=raw.close.shift(1);rate=ordinary_limit_rate(code,dates)
  limitprice=np.floor(prev*(1+rate)*100+.50000001)/100
  consistent=(ret-raw.close.pct_change(fill_method=None)).abs()<.002
  candidate=(((raw.close-limitprice).abs()<.001)&consistent&valid&(observed>20)).where(ret.notna())
  d=pd.DataFrame({'ret':ret,'candidate':candidate,'breadth':(ret>0).where(ret.notna()),'above20':(q.close>q.close.rolling(20,min_periods=20).mean()).where(q.close.rolling(20).mean().notna()),'volratio':raw.volume/raw.volume.shift(1).rolling(5,min_periods=5).mean()},index=dates)
  for h in [1,3,5,10]:d[f'fwd{h}']=forward_open(q.open,h)
  stocks[code]=d;quality.append({'symbol':code,'name':name,'status':'ok','overlap_rows':int(q.close.notna().sum()),'first':q.close.first_valid_index(),'last':q.close.last_valid_index()})
 pd.DataFrame(quality).to_csv(OUT/'historical_stock_coverage.csv',index=False)
 frames=[];coverage=[];bmret=bench.close.pct_change(fill_method=None)
 for sector,info in frozen['sectors'].items():
  codes=[c for c in info['stocks'] if c in stocks];N=info['eligible_denominator'];fraction=len(codes)/N
  coverage.append({'sector_code':sector,'sector_name':info['sector_name'],'eligible_members':N,'historically_usable':len(codes),'coverage':fraction})
  if fraction<.95 or len(codes)<8:continue
  fields=['ret','candidate','breadth','above20','volratio']+[f'fwd{h}' for h in [1,3,5,10]]
  mx={k:pd.concat({c:stocks[c][k] for c in codes},axis=1).astype(float) for k in fields}
  cov=mx['ret'].notna().sum(axis=1)/N;ok=cov>=.95
  d=pd.DataFrame(index=dates);d['ret']=mx['ret'].mean(axis=1).where(ok);d['excess']=d.ret-bmret;d['breadth']=mx['breadth'].mean(axis=1).where(ok);d['above20']=mx['above20'].mean(axis=1).where(mx['above20'].notna().sum(axis=1)/N>=.95)
  d['L']=mx['candidate'].sum(axis=1).where(ok);d['N']=N;d['coverage']=cov;d['volratio']=mx['volratio'].clip(.05,10).mean(axis=1).where(ok)
  for h in [1,3,5,10]:d[f'y{h}']=(mx[f'fwd{h}'].mean(axis=1)-forward_open(bench.open,h)).where(mx[f'fwd{h}'].notna().sum(axis=1)/N>=.95)
  d['sector_code']=sector;d['sector_name']=info['sector_name'];d['date']=dates
  frames.append(d.reset_index(drop=True))
 if len(frames)<8:raise RuntimeError(f'Only {len(frames)} sufficiently covered sectors')
 panel=pd.concat(frames,ignore_index=True).sort_values(['date','sector_code']).reset_index(drop=True)
 market=pd.concat({c:d.candidate for c,d in stocks.items()},axis=1).astype(float).mean(axis=1).rename('market_p');market.index.name='date'
 panel=panel.merge(market,on='date');panel['excess_rank']=panel.groupby('date').excess.rank(pct=True)
 r=full_bench.close.pct_change(fill_method=None);vol=r.rolling(20,min_periods=20).std();pastmedian=vol.shift(1).rolling(252,min_periods=126).median()
 regime=pd.DataFrame({'date':full_bench.index,'trend':np.tanh(10*np.log(full_bench.close/full_bench.close.rolling(60,min_periods=60).mean())),'stress':np.tanh(np.log(vol/pastmedian))}).reset_index(drop=True)
 panel=panel.merge(regime,on='date',how='left')
 date_series=pd.Series(dates,index=dates)
 for h in [1,3,5,10]:panel=panel.merge(date_series.shift(-(h+1)).rename(f'exit{h}'),left_on='date',right_index=True,how='left')
 panel['regime']=np.where(panel.stress>.25,'high_vol',np.where(panel.trend>.10,'uptrend',np.where(panel.trend<-.10,'downtrend','range')))
 pd.DataFrame(coverage).to_csv(OUT/'historical_sector_coverage.csv',index=False);panel.to_parquet(OUT/'historical_panel.parquet',index=False)
 print('PANEL',panel.shape,'sectors',panel.sector_code.nunique(),'stocks',len(stocks),flush=True)
 return panel

FEATURES=['M','H','C','B','F','A','D','H2','C2','M2','H_B','H_C','H_D','M_A','B_A','trend_M','trend_H','stress_H','stress_B']
def enrich(f):
 g=f.copy()
 for base in ['H','C','M']:g[base+'2']=g[base]**2
 for a,b in [('H','B'),('H','C'),('H','D'),('M','A'),('B','A'),('trend','M'),('trend','H'),('stress','H'),('stress','B')]:g[f'{a}_{b}']=g[a]*g[b]
 return g

def ridge_fit(X,y,alpha):
 X=np.asarray(X,float);y=np.asarray(y,float);mean=X.mean(axis=0);scale=X.std(axis=0);scale[scale<1e-10]=1.;z=(X-mean)/scale;ym=y.mean()
 beta=np.linalg.solve(z.T@z+alpha*np.eye(X.shape[1]),z.T@(y-ym));return {'mean':mean,'scale':scale,'beta':beta,'intercept':ym,'alpha':alpha}
def ridge_predict(fit,X):return ((np.asarray(X,float)-fit['mean'])/fit['scale'])@fit['beta']+fit['intercept']
def metrics(f,scorecol,mask,h=5):
 rows=[]
 for date,g in f.loc[mask,['date','sector_code',scorecol,f'y{h}','regime']].dropna().groupby('date',sort=True):
  if len(g)<8:continue
  order=g.sort_values([scorecol,'sector_code']);k=max(2,math.ceil(len(g)*.2));r=g[scorecol].rank();y=g[f'y{h}'].rank()
  ic=r.corr(y) if r.nunique()>1 and y.nunique()>1 else np.nan
  rows.append({'date':date,'n':len(g),'ic':ic,'top_excess':order.iloc[-k:][f'y{h}'].mean(),'bottom_excess':order.iloc[:k][f'y{h}'].mean(),'spread':order.iloc[-k:][f'y{h}'].mean()-order.iloc[:k][f'y{h}'].mean(),'regime':g.regime.iloc[0]})
 return pd.DataFrame(rows)
def summarize(d):
 if d.empty:return {'days':0}
 return {'days':len(d),'mean_ic':float(d.ic.mean()),'ic_95_ci':v1.block_ci(d.ic,n=1000),'mean_top_excess':float(d.top_excess.mean()),'mean_spread':float(d.spread.mean()),'spread_95_ci':v1.block_ci(d.spread,n=1000),'top_outperforms_rate':float((d.top_excess>0).mean())}
def period_mask(f,start,end,h=5):return f.date.between(start,end)&f[f'exit{h}'].notna()&(f[f'exit{h}']<=end)

def validate(panel):
 fs=v1.features(panel,5,2,False);linear=v1.features(panel,3,1,True);fixed=v1.features(panel,3,1,False)
 assert fs[['date','sector_code']].equals(linear[['date','sector_code']]);assert fs[['date','sector_code']].equals(fixed[['date','sector_code']])
 g=enrich(fs);g['v1_linear_frozen']=v1.score(linear,v1.WEIGHTS['balanced'],False);g['v1_selected_frozen']=v1.score(fs,v1.WEIGHTS['limit'],True)
 heat=fixed.copy();heat.H=heat.H*np.exp(-heat.H/.35)/(.35/np.e);g['nonmonotonic_heat']=v1.score(heat,v1.WEIGHTS['balanced'],True)
 # A single observation mask is frozen for ALL four models, independent of fit/outcome.
 common=g.eligible_score&linear.eligible_score&fixed.eligible_score&g[FEATURES].notna().all(axis=1)
 for c in MODELS[:-1]:g[c]=g[c].where(common)
 g['eligible_common']=common;g['walk_forward_ridge']=np.nan
 yearly=[];alpha_rows=[];coefficients=[];all_daily=[];checks=[];primary_rows=[];horizon_rows=[]
 for year in [2020,2021,2022,2023]:
  train_end=f'{year-2}-12-31';val_start=f'{year-1}-01-01';val_end=f'{year-1}-12-31';test_start=f'{year}-01-01';test_end=f'{year}-12-31'
  train=common&period_mask(g,'2017-01-01',train_end)&g.y5.notna();val=common&period_mask(g,val_start,val_end)&g.y5.notna();test=common&period_mask(g,test_start,test_end)&g.y5.notna()
  # Keep cross-sectional target/evaluation days with at least eight industries.
  for mask in [train,val,test]:
   counts=g.loc[mask].groupby('date').sector_code.transform('size');mask.loc[mask]=counts>=8
  if min(g.loc[train].date.nunique(),g.loc[val].date.nunique(),g.loc[test].date.nunique())<60:
   checks.append({'year':year,'status':'skipped_insufficient_history'});continue
  assert g.loc[train,'exit5'].max()<=train_end;assert g.loc[val,'exit5'].max()<=val_end;assert g.loc[train,'date'].max()<val_start;assert g.loc[val,'date'].max()<test_start
  tr=g.loc[train].copy();target=tr.groupby('date').y5.rank(pct=True);target=target-target.groupby(tr.date).transform('mean')
  fitted={};vals=[]
  for alpha in [1.,10.,100.]:
   fit=ridge_fit(tr[FEATURES],target,alpha);fitted[alpha]=fit;temp=g.copy();temp['trial']=np.nan;temp.loc[val,'trial']=ridge_predict(fit,g.loc[val,FEATURES]);dm=metrics(temp,'trial',val)
   value=float(dm.ic.mean());vals.append((value,alpha));alpha_rows.append({'test_year':year,'alpha':alpha,'validation_ic':value,'train_rows':int(train.sum()),'validation_rows':int(val.sum()),'train_last_label_exit':g.loc[train,'exit5'].max(),'validation_last_label_exit':g.loc[val,'exit5'].max()})
  chosen=max(vals,key=lambda x:(x[0],x[1]))[1];fit=fitted[chosen]
  # Refit is intentionally omitted: use the same pre-validation fit whose alpha won validation.
  predict_mask=common&g.date.between(test_start,test_end)
  g.loc[predict_mask,'walk_forward_ridge']=ridge_predict(fit,g.loc[predict_mask,FEATURES])
  for feature,b,mu,sd in zip(FEATURES,fit['beta'],fit['mean'],fit['scale']):coefficients.append({'test_year':year,'feature':feature,'standardized_beta':float(b),'train_mean':float(mu),'train_scale':float(sd),'alpha':chosen})
  for c in MODELS:assert g.loc[test,c].notna().all(),f'missing scores in {c}'
  for model in MODELS:
   dm=metrics(g,model,test);dm['model']=model;dm['test_year']=year;all_daily.append(dm)
   yearly.append({'test_year':year,'model':model,'alpha':chosen if model=='walk_forward_ridge' else None,**summarize(dm)})
   for h in [1,3,5,10]:
    hm=common&period_mask(g,test_start,test_end,h)&g[f'y{h}'].notna()&g.walk_forward_ridge.notna()
    dd=metrics(g,model,hm,h);horizon_rows.append({'test_year':year,'model':model,'horizon':h,'days':len(dd),'mean_ic':float(dd.ic.mean()) if len(dd) else None,'mean_top_excess':float(dd.top_excess.mean()) if len(dd) else None,'mean_spread':float(dd.spread.mean()) if len(dd) else None})
  checks.append({'year':year,'status':'ok','train_signal_start':g.loc[train,'date'].min(),'train_signal_end':g.loc[train,'date'].max(),'train_latest_label_exit':g.loc[train,'exit5'].max(),'validation_signal_start':g.loc[val,'date'].min(),'validation_signal_end':g.loc[val,'date'].max(),'validation_latest_label_exit':g.loc[val,'exit5'].max(),'test_signal_start':g.loc[test,'date'].min(),'test_signal_end':g.loc[test,'date'].max(),'test_days':g.loc[test,'date'].nunique(),'alpha':chosen,'validation_best_ic':max(vals)[0]})
  print('FOLD',year,'alpha',chosen,'IC',[(r['model'],round(r['mean_ic'],4)) for r in yearly if r['test_year']==year],flush=True)
 # The old period is excluded entirely; all outputs are new historical retrospective tests.
 if not all_daily:raise RuntimeError('No fold passed coverage requirements')
 daily=pd.concat(all_daily,ignore_index=True);daily.to_csv(OUT/'daily_metrics.csv',index=False);pd.DataFrame(yearly).to_csv(OUT/'yearly_results.csv',index=False);pd.DataFrame(alpha_rows).to_csv(OUT/'validation_alpha_selection.csv',index=False);pd.DataFrame(coefficients).to_csv(OUT/'ridge_coefficients.csv',index=False);pd.DataFrame(horizon_rows).to_csv(OUT/'horizon_results.csv',index=False)
 aggregate=[]
 for model,d in daily.groupby('model'):aggregate.append({'model':model,**summarize(d)})
 paired=[]
 base=daily[daily.model=='v1_selected_frozen']
 for model in ['v1_linear_frozen','nonmonotonic_heat','walk_forward_ridge']:
  pair=daily[daily.model==model].merge(base,on=['date','test_year'],suffixes=('_model','_frozen'))
  assert len(pair)==len(base);assert (pair.n_model==pair.n_frozen).all()
  paired.append({'model':model,'against':'v1_selected_frozen','days':len(pair),'ic_delta':float((pair.ic_model-pair.ic_frozen).mean()),'ic_delta_95_ci':v1.block_ci(pair.ic_model-pair.ic_frozen,n=1000),'spread_delta':float((pair.spread_model-pair.spread_frozen).mean()),'spread_delta_95_ci':v1.block_ci(pair.spread_model-pair.spread_frozen,n=1000)})
 regime=[]
 for (model,r),d in daily.groupby(['model','regime']):regime.append({'model':model,'regime':r,**summarize(d)})
 dump(OUT/'aggregate_results.json',aggregate);dump(OUT/'paired_results.json',paired);dump(OUT/'regime_results.json',regime);dump(OUT/'fold_checks.json',checks)
 g.to_parquet(OUT/'scored_history.parquet',index=False)
 # Causal prefix check within a fixed historical universe; source/selection biases remain disclosed.
 cut='2021-06-30';prefix=v1.features(panel[panel.date<=cut],5,2,False);past=fs[fs.date<=cut].reset_index(drop=True)
 assert prefix[['date','sector_code']].equals(past[['date','sector_code']])
 for c in ['M','H','C','B','F','A','D']:np.testing.assert_allclose(prefix[c],past[c],equal_nan=True,rtol=1e-10)
 dump(OUT/'verification.json',{'feature_prefix_invariance':'passed','future_label_maturity':'passed','train_only_scaling':'passed','all_model_observations_identical':'passed','prior_v1_period_not_reused_as_fresh_test':'passed','spec_sha256':hashlib.sha256((ROOT/'experiment_spec.json').read_bytes()).hexdigest()})
 print('AGGREGATE',json.dumps(aggregate,ensure_ascii=False),flush=True);return aggregate,yearly,paired,daily

def report(panel,aggregate,yearly,paired,daily):
 import matplotlib
 matplotlib.use('Agg');import matplotlib.pyplot as plt
 ys=pd.DataFrame(yearly);fig,ax=plt.subplots(figsize=(9,4.5))
 labels={'v1_linear_frozen':'Linear baseline','v1_selected_frozen':'Frozen v1','nonmonotonic_heat':'Nonmonotonic heat','walk_forward_ridge':'Walk-forward ridge'}
 for model in MODELS:
  d=ys[ys.model==model];ax.plot(d.test_year,d.mean_ic,marker='o',label=labels[model])
 ax.axhline(0,color='#777',lw=1);ax.set(xlabel='Retrospective test year',ylabel='Mean daily 5-session Rank IC',title='Frozen specifications · current-constituent historical baskets');ax.legend();fig.tight_layout();fig.savefig(OUT/'yearly_ic.png',dpi=160);plt.close(fig)
 meta=json.loads((ROOT/'download_summary.json').read_text());audit=pd.DataFrame(meta['results']);audit.to_csv(OUT/'source_identity_and_overlap_audit.csv',index=False)
 compared=audit[audit.tencent_overlap.fillna(0)>0] if 'tencent_overlap' in audit else pd.DataFrame()
 info={'stocks_downloaded':meta['ok'],'stock_source_bars':int(audit.loc[audit.status=='ok','rows'].sum()),'industries':int(panel.sector_code.nunique()),'panel_rows':len(panel),'source_crosscheck_stocks':len(compared),'crosscheck_exact_fraction_mean':float(compared.close_exact_fraction.mean()) if len(compared) else None,'historical_panel_first':panel.date.min(),'historical_panel_last':panel.date.max(),'source':'Sina full-history source from repository akshare fallback; all adjusted with archived Sina qfq factors; Tencent only independent overlap check','old_period_already_seen':'2024-03-13..2026-11-03','test_type':'historical retrospective holdouts, not prospective verification','limitations':['current constituent and survival selection bias','historical ST and exchange limit-price states unavailable; candidate-only limits','historical sector baskets are not official Eastmoney indices','current archived qfq factors, no historical publication snapshots','no fees, slippage, portfolio capacity or executable fills','predeclared new models motivated by prior-seen later period; historical reuse is not fully independent prospective evidence'],'aggregate':aggregate,'paired':paired}
 dump(OUT/'results.json',info)
 lines=['# 真实数据第二轮验证','',f"下载 {info['stocks_downloaded']} 只股票的新浪原始历史，共 {info['stock_source_bars']:,} 条记录；进入面板 {info['industries']} 个行业，面板日期 {info['historical_panel_first']} 至 {info['historical_panel_last']}。",'',f"原始新浪压缩数据符号头逐只核验；{len(compared)}只股票与上轮腾讯未复权数据作交叉核对。来源日期截断保留，抓取时间不冒充源市场时间。",'', '本轮仅使用2017年起的数据训练、2020～2023年逐年回溯留出。上轮2024～2026年数据不重新作为新测试集。每个年度的训练/验证标签必须在各自区间结束前成熟；ridge标准化只拟合训练集；alpha只按前一年验证IC选择。新模型由上轮结果启发，且当前成分股回看历史存在偏差，因此本轮不能称为严格前瞻验证。','','| 模型 | 测试日期数 | 5日平均IC | IC 95%分块区间 | 高−低5日收益差 |','|---|---:|---:|---|---:|']
 for r in aggregate:lines.append(f"| {r['model']} | {r['days']} | {r['mean_ic']:.4f} | {r['ic_95_ci']} | {r['mean_spread']*100:.3f}个百分点 |")
 lines+=['','逐年结果：','','| 年份 | 模型 | IC | 高−低收益差 |','|---|---|---:|---:|']
 for r in yearly:lines.append(f"| {r['test_year']} | {r['model']} | {r['mean_ic']:.4f} | {r['mean_spread']*100:.3f}个百分点 |")
 lines+=['','![逐年IC](yearly_ic.png)','','所有数字为次日开盘开始的重叠事件统计，未计成本，不是组合年化收益。配对改善区间见 paired_results.json；分市场状态诊断见 regime_results.json。','', '## 数据限制','']+[f'- {x}' for x in info['limitations']]+['','涨停池请求20261103但响应qdate=20261104，已拒绝用其核验涨停；所有涨停字段仍标为候选。创业板2020-08-24前后10%/20%制度变化按日期处理。','','输出包含原始压缩行情、解码缓存、请求身份与哈希、实验预登记、训练/验证/测试边界、标准化与权重、所有模型逐日结果。没有自动修改生产规则。']
 (OUT/'report.md').write_text('\n'.join(lines))
 dump(OUT/'raw_source_manifest.json',[{'file':str(p.relative_to(ROOT)),'bytes':p.stat().st_size,'sha256':hashlib.sha256(p.read_bytes()).hexdigest()} for p in sorted((ROOT/'raw_sina').glob('*'))])
if __name__=='__main__':
 panel=build_panel();aggregate,yearly,paired,daily=validate(panel);report(panel,aggregate,yearly,paired,daily)
