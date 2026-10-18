"""Refresh archived ST packets from new responses, preserving historical evidence."""
import argparse
import concurrent.futures
import json
import re
from datetime import datetime
from pathlib import Path
from st_research_collect import Client, TZ, current_universe, notices, document, FIN, ordinary

def refresh_one(client, path, as_of, since):
    packet=json.loads(path.read_text(encoding='utf-8'))
    code=packet['code']; errors=[]
    try:
        rows, coverage=notices(client,code,since,as_of,6)
        if not coverage['complete']:errors.append(coverage['error'])
        old={r['art_code']:r for r in packet.get('announcement_titles',[])}
        old.update({r['art_code']:{k:r.get(k) for k in ('title','notice_date','art_code','display_time','eiTime')} for r in rows})
        packet['announcement_titles']=sorted(old.values(),key=lambda r:r['notice_date'],reverse=True)
        docs={d['id']:d for d in packet.get('documents',[])}
        for row in rows:
            if re.search(r'风险警示|行政处罚|立案|清偿|偿还.*占用|内控.*整改|业绩预告|业绩快报',row['title']):
                try:docs[row['art_code']]=document(client,code,row,16)
                except Exception as exc:errors.append(row['art_code']+': '+str(exc))
        packet['documents']=sorted(docs.values(),key=lambda d:d['date'],reverse=True)
        secucode=packet.get('secucode') or code+('.SH' if code.startswith('6') else '.SZ')
        value=client.get(FIN,dict(type='RPT_F10_FINANCE_MAINFINADATA',sty='APP_F10_MAINFINADATA',filter=f'(SECUCODE="{secucode}")',p=1,ps=20,sr=-1,st='REPORT_DATE',source='HSF10',client='PC'))
        records=(value.get('result') or {}).get('data')
        if not records:raise ValueError('financial data unavailable')
        fields=['REPORT_DATE','NOTICE_DATE','UPDATE_DATE','REPORT_TYPE','SECURITY_NAME_ABBR','PARENTNETPROFIT','KCFJCXSYJLR','TOTALOPERATEREVE','PARENTNETPROFITTZ','KCFJCXSYJLRTZ','MGJYXJJE','BPS','ZCFZL']
        packet['financials']=[{k:r.get(k) for k in fields} for r in records if str(r.get('NOTICE_DATE',''))[:10]<=as_of]
        q=client.get('https://qt.gtimg.cn/q='+secucode.split('.')[1].lower()+code,text=True,encoding='gbk')
        bits=q.split('"')[1].split('~')
        if bits[2]!=code:raise ValueError('quote identity mismatch')
        packet['market']=dict(name=bits[1],price=bits[3],time=bits[30])
        packet['name']=bits[1];packet['current_st']=ordinary(bits[1])
    except Exception as exc:errors.append(str(exc))
    packet['freshness_check']=dict(checked_at=datetime.now(TZ).isoformat(),as_of=as_of,since=since,errors=errors)
    path.write_text(json.dumps(packet,ensure_ascii=False,indent=2),encoding='utf-8')
    return dict(code=code,name=packet['name'],errors=errors,documents=len(packet.get('documents',[])),checked_at=packet['freshness_check']['checked_at'])

def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('root',type=Path);p.add_argument('--project',required=True);p.add_argument('--as-of',required=True);p.add_argument('--since',required=True);p.add_argument('--symbols');a=p.parse_args()
    try:
        for value in (a.as_of,a.since):datetime.strptime(value,'%Y-%m-%d')
    except ValueError:p.error('日期必须为YYYY-MM-DD')
    if a.since>a.as_of or a.as_of>datetime.now(TZ).date().isoformat():p.error('日期范围无效或晚于今天')
    if not (a.root/'companies').is_dir():p.error('缺少companies原始材料目录，请先采集')
    stamp=datetime.now(TZ).strftime('%Y%m%d_%H%M%S_%f')
    universe_error=None
    client=Client(a.root/'refresh'/stamp,a.project)
    try:
        current=current_universe(client)
        (a.root/'universe_current.json').write_text(json.dumps(current,ensure_ascii=False,indent=2),encoding='utf-8')
    except Exception as exc:
        universe_error=str(exc)
        print('CURRENT UNIVERSE FAILED: '+universe_error,flush=True)
    paths=sorted((a.root/'companies').glob('*.json'))
    if a.symbols:paths=[p for p in paths if p.stem in a.symbols.split(',')]
    results=[]
    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
        fs=[pool.submit(refresh_one,client,path,a.as_of,a.since) for path in paths]
        for f in concurrent.futures.as_completed(fs):
            r=f.result();results.append(r);print(json.dumps(r,ensure_ascii=False),flush=True)
    result=dict(as_of=a.as_of,checked_at=datetime.now(TZ).isoformat(),companies=results,raw_archive=str(client.root.relative_to(a.root)),universe_error=universe_error,scope='subset' if a.symbols else 'all_archived_companies')
    (a.root/'freshness.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
if __name__=='__main__':main()
