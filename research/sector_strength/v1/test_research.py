"""Math/causality checks use synthetic fixtures only; never included in market analysis."""
import importlib.util
from pathlib import Path
import numpy as np
import pandas as pd
spec=importlib.util.spec_from_file_location('research',Path(__file__).with_name('analyze.py'));r=importlib.util.module_from_spec(spec);spec.loader.exec_module(r)
def fixture():
 dates=pd.bdate_range('2024-01-01',periods=150).strftime('%Y-%m-%d');rng=np.random.default_rng(321);out=[]
 for i in range(10):
  n=20+i; e=rng.normal(0,.01,len(dates));d=pd.DataFrame({'date':dates,'sector_code':f's{i}','excess':e,'ret':e+.001,'L':rng.integers(0,4,len(dates)),'N':n,'market_p':.02,'breadth':rng.uniform(.2,.8,len(dates)),'above20':rng.uniform(.2,.8,len(dates)),'volratio':rng.uniform(.5,2,len(dates))});out.append(d)
 p=pd.concat(out,ignore_index=True);p['excess_rank']=p.groupby('date').excess.rank(pct=True);return p

def test_future_information_does_not_change_past_scores():
 p=fixture();cut=sorted(p.date.unique())[100];a=r.features(p,3,2);changed=p.copy();changed.loc[changed.date>cut,['excess','ret','L','breadth','above20','volratio']]=1000;b=r.features(changed,3,2)
 cols=['M','H','C','B','F','A','D'];np.testing.assert_allclose(a.loc[a.date<=cut,cols],b.loc[b.date<=cut,cols],equal_nan=True)

def test_score_stays_finite_and_bounded_for_valid_features():
 f=r.features(fixture(),3,2);s=r.score(f,r.WEIGHTS['balanced']);s=s.dropna();assert len(s)>500;assert s.between(0,100).all()

def test_log_diminishing_returns():
 n=60
 contributions=np.log1p(np.arange(0,n+1))/np.log1p(n)
 increments=np.diff(contributions);assert (increments>0).all();assert (np.diff(increments)<0).all()

def test_halflife_decay():
 x=pd.Series([1.]+[0.]*12);s=x.ewm(halflife=3,adjust=False).mean();np.testing.assert_allclose(s.iloc[[3,6,9]],[.5,.25,.125])

def test_squared_persistence_rewards_concentrated_strong_evidence():
 # On [0,1], squaring suppresses sporadic strength more than consistent strength.
 weak,strong=.2,.8
 assert (strong**2)/(weak**2)>strong/weak

def test_event_metric_rejects_too_small_cross_sections():
 f=pd.DataFrame({'date':['2025-01-01']*7,'sector_code':list('abcdefg'),'score':range(7),'y5':np.arange(7)/100})
 assert r.daily_metrics(f,'score',np.ones(len(f),dtype=bool)).empty


def test_split_adjustment_uses_effective_factor_without_backfilling_future():
 raw=pd.DataFrame({'date':['2025-01-01','2025-01-02','2025-01-03'],'open':[100.,102.,51.],'high':[100.,102.,51.],'low':[100.,102.,51.],'close':[100.,102.,51.]})
 factors=[{'d':'2025-01-01','f':'2'},{'d':'2025-01-03','f':'1'}]
 q,_=r.adjust_ohlc(raw,factors,'2025-01-03')
 np.testing.assert_allclose(q.close,[50,51,51])
 assert q.close.pct_change().iloc[-1]==0
 late=[{'d':'2025-01-03','f':'1'}]
 q,_=r.adjust_ohlc(raw,late,'2025-01-03');assert q.close.iloc[:2].isna().all()


def test_models_and_ablations_share_missing_observation_mask():
 p=fixture();c=(p.sector_code=='s0')&(p.date==sorted(p.date.unique())[100]);p.loc[c,'breadth']=np.nan
 linear=r.features(p,3,1,True);nonlinear=r.features(p,5,2,False)
 a=r.score(linear,r.WEIGHTS['balanced'],False);b=r.score(nonlinear,r.WEIGHTS['limit'],True)
 assert a.notna().equals(b.notna())
 ablated=nonlinear.copy();ablated['A']=0;ablated['D']=0
 c=r.score(ablated,r.WEIGHTS['limit'],True);assert b.notna().equals(c.notna())
