"""Synthetic fixtures only for math tests. Never used as market observations."""
import importlib.util
from pathlib import Path
import numpy as np,pandas as pd
spec=importlib.util.spec_from_file_location('v2',Path(__file__).with_name('validate.py'));m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
def test_chinext_historical_rule_change():
 r=m.ordinary_limit_rate('300001',['2020-08-21','2020-08-24','2021-01-04']);np.testing.assert_allclose(r,[.1,.2,.2])
def test_main_board_remains_ten_percent():
 np.testing.assert_allclose(m.ordinary_limit_rate('600000',['2017-01-03','2023-12-29']),[.1,.1])
def test_forward_label_starts_at_next_open():
 s=pd.Series([10.,20.,30.,40.,50.,60.,70.]);y=m.forward_open(s,1);assert y.iloc[0]==.5
 np.testing.assert_allclose(m.forward_open(s,5).iloc[0],2.5)
def test_label_exit_must_be_matured_before_boundary():
 f=pd.DataFrame({'date':['2019-12-20','2019-12-27'],'exit5':['2019-12-30','2020-01-08']});np.testing.assert_array_equal(m.period_mask(f,'2019-01-01','2019-12-31'),[True,False])
def test_ridge_training_scaler_does_not_read_future():
 x=np.array([[0,2],[1,4],[2,6],[3,8]],float);y=np.array([0,1,2,3.]);f=m.ridge_fit(x,y,1.)
 np.testing.assert_allclose(f['mean'],[1.5,5.]);p=m.ridge_predict(f,x);future=np.array([[1e9,-1e9]])
 m.ridge_predict(f,future);np.testing.assert_allclose(m.ridge_predict(f,x),p)
def test_heat_curve_has_peak_and_declines_after_peak():
 tau=.35;heat=np.array([.1,.35,.9]);z=heat*np.exp(-heat/tau)/(tau/np.e);assert z[1]>z[0];assert z[1]>z[2];np.testing.assert_allclose(z[1],1.)
def test_ridge_can_fit_negative_relation_without_manual_inversion():
 x=np.arange(20,dtype=float).reshape(-1,1);y=-x[:,0];fit=m.ridge_fit(x,y,1.);assert fit['beta'][0]<0;assert np.corrcoef(m.ridge_predict(fit,x),y)[0,1]>.999
