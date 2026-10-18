"""Collect a reproducible ordinary-ST research packet; no trading or probability claims."""
import argparse
import concurrent.futures
import hashlib
import json
from pathlib import Path
import re
import threading
import time
from datetime import datetime, timedelta, timezone
from urllib.parse import urlencode

TZ = timezone(timedelta(hours=8))
DC = 'https://datacenter-web.eastmoney.com/api/data/v1/get'
ANN = 'https://np-anotice-stock.eastmoney.com/api/security/ann'
CONTENT = 'https://np-cnotice-stock.eastmoney.com/api/content/ann'
FIN = 'https://datacenter.eastmoney.com/securities/api/data/get'


def ordinary(name):
    return re.sub(r'\s+', '', name).upper().startswith('ST')


class Client:
    def __init__(self, root, project=None):
        self.root = Path(root)
        self.raw = self.root / 'raw'
        self.raw.mkdir(parents=True, exist_ok=True)
        self.lock = threading.Lock()
        self.last = 0
        self.network_get = None
        self.network_text = None
        if not project:
            raise ValueError('需要eastmoney项目网络层；请提供--project <eastmoney-quant-mcp根目录>')
        if project:
            import sys
            sys.path.insert(0, str(Path(project) / 'src'))
            from stock_analysis_mcp.data.network import http_get, http_get_text
            self.network_get, self.network_text = http_get, http_get_text

    def get(self, url, params=None, text=False, encoding=None):
        full = url + ('?' + urlencode(params) if params else '')
        key = hashlib.sha256(full.encode()).hexdigest()
        path = self.raw / (key + '.json')
        if path.exists():
            return json.loads(path.read_text(encoding='utf-8'))['response']
        with self.lock:
            time.sleep(max(0, .3 - (time.monotonic() - self.last)))
            self.last = time.monotonic()
        if self.network_get:
            fn = self.network_text if text else self.network_get
            kw = dict(params=params, retries=2, timeout=15, use_cookies=False, verify=True)
            if text:
                kw['encoding'] = encoding
            value = fn(url, **kw)
            if value is None:
                raise RuntimeError('network returned no response: ' + full[:180])
        temp = path.with_suffix('.tmp')
        temp.write_text(json.dumps(dict(url=full, fetched_at=datetime.now(TZ).isoformat(), response=value), ensure_ascii=False), encoding='utf-8')
        temp.replace(path)
        return value


def universe(client, period):
    rows, total, pages = [], None, None
    for page in range(1, 101):
        value = client.get(DC, dict(reportName='RPT_DMSK_FN_INCOME', columns='SECUCODE,SECURITY_CODE,SECURITY_NAME_ABBR,SECURITY_TYPE_CODE,TRADE_MARKET_CODE,REPORT_DATE,NOTICE_DATE',
                                   pageSize=500, pageNumber=page, filter=f"(REPORT_DATE='{period}')", sortColumns='SECURITY_CODE', sortTypes='1'))
        if not value.get('success'):
            raise ValueError('universe response failed')
        data = value['result']
        if total is not None and (total != data['count'] or pages != data['pages']):
            raise ValueError('universe changed during pagination')
        total, pages = data['count'], data['pages']
        rows.extend(data['data'])
        if page >= pages:
            break
    if len(rows) != total or len({r['SECUCODE'] for r in rows}) != total:
        raise ValueError('universe incomplete or duplicate')
    # The financial directory is a discovery universe, not an exchange-certified current list.
    candidates = [r for r in rows if ordinary(r['SECURITY_NAME_ABBR']) and r['SECURITY_TYPE_CODE'] == '058001001']
    result = dict(period=period, total=total, pages=pages, directory_complete=True,
                  exchange_universe_verified=False, candidates=candidates,
                  limitations=['财务报告目录不等于交易所当日全体证券名单；未交该期财报的公司可能缺失。'])
    (client.root / 'universe.json').write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
    return result


def current_universe(client):
    rows, count, seen = [], None, set()
    for page in range(1, 11):
        value=client.get('https://push2.eastmoney.com/webguest/api/qt/clist/get',dict(pn=page,pz=100,fs='b:BK0511',fields='f12,f14,f13',fid='f12',po=0))
        data=value.get('data')
        if not data or value.get('rc')!=0:
            raise ValueError('current ST directory unavailable')
        if count is not None and count!=data['total']:
            raise ValueError('ST directory changed during pagination')
        count=data['total']
        received=data['diff']
        if isinstance(received,dict):received=list(received.values())
        if not received and len(rows)<count:raise ValueError('ST directory premature empty page')
        for row in received:
            key=(row['f12'],row['f13'])
            if key in seen:raise ValueError('ST directory duplicate page')
            seen.add(key);rows.append(row)
        if len(rows)>=count:break
    if len(rows)!=count:raise ValueError('ST directory pagination incomplete')
    ordinary_rows=[r for r in rows if ordinary(r['f14']) and r['f13'] in (0,1) and re.fullmatch(r'[036]\d{5}',r['f12'])]
    result=dict(source='东方财富ST板块BK0511',fetched_at=datetime.now(TZ).isoformat(),
                total_with_star=count,ordinary_count=len(ordinary_rows),pages=page,complete=True,
                exchange_certified=False,beijing_coverage_verified=False,
                stocks=[dict(code=r['f12'],name=r['f14'],market=r['f13']) for r in ordinary_rows])
    (client.root/'universe_current.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    return result


def publication_day(row):
    """Availability date differs from the notice's printed/effective date."""
    value = row.get('display_time') or row.get('eiTime') or row.get('notice_date') or row.get('date') or ''
    return str(value)[:10]


def notices(client, symbol, since, until, max_pages):
    rows, ids, complete, error = [], set(), False, None
    last = None
    for page in range(1, max_pages + 1):
        value = client.get(ANN, dict(page_size=100, page_index=page, ann_type='A', client_source='web', sr=-1, stock_list=symbol))
        if value.get('success') != 1:
            raise ValueError('announcement response failed')
        data = value['data']
        received = data['list']
        dates = [x['notice_date'][:10] for x in received]
        if not received:
            if len(ids) < int(data['total_hits']):
                raise ValueError('premature empty announcement page')
            complete = True
            break
        if dates != sorted(dates, reverse=True) or (last and dates[0] > last):
            raise ValueError('announcement dates are not ordered')
        for row in received:
            if row['art_code'] in ids:
                raise ValueError('duplicate announcement page')
            ids.add(row['art_code'])
            if since <= publication_day(row) <= until:
                rows.append(row)
        last = dates[-1]
        if last < since or len(ids) >= int(data['total_hits']):
            complete = True
            break
    if not complete:
        error = '公告达到分页上限，戴帽起点或历史进展可能缺失'
    return rows, dict(pages=page, complete=complete, since=since, until=until, error=error)


def select_documents(rows, cap):
    chosen = {}
    def add(items, limit):
        for x in items[:limit]:
            chosen[x['art_code']] = x
    # Risk-origin and recent progress are both needed; latest-only selection misses the original trigger.
    risk = [r for r in rows if re.search(r'风险警示|摘帽|行政处罚决定|行政处罚事先|立案调查|整改|会计差错|追溯', r['title'])]
    add(risk, 10)
    add(sorted([r for r in risk if re.search(r'实施.*风险警示.*停|被实施.*风险警示', r['title'])], key=lambda x:x['notice_date']), 3)
    add([r for r in risk if '行政处罚决定' in r['title']], 2)
    add([r for r in rows if re.search(r'清偿|偿还.*占用|占用.*偿还|解除.*冻结|担保.*解除', r['title'])], 3)
    add([r for r in rows if re.search(r'内部控制审计报告|关于.*审计意见|非标准.*审计', r['title'])], 2)
    add([r for r in rows if re.search(r'20\d{2}年年度报告摘要|20\d{2}年半年度报告摘要|20\d{2}年度报告摘要', r['title'])], 2)
    add([r for r in rows if re.search(r'业绩预告|业绩快报|业绩说明会|投资者关系活动记录', r['title'])], 2)
    return list(chosen.values())[:cap]


def document(client, code, row, max_parts=8):
    art=row.get('art_code') or row.get('id')
    first=client.get(CONTENT,dict(art_code=art,page_index=1,client_source='web'))
    data=first.get('data') or {}
    if first.get('success')!=1 or data.get('art_code')!=art:
        raise ValueError('content identity mismatch')
    parts=int(data.get('page_size') or 1)
    if parts<1:raise ValueError('invalid content part count')
    text=[data.get('notice_content') or '']
    for index in range(2,min(parts,max_parts)+1):
        response=client.get(CONTENT,dict(art_code=art,page_index=index,client_source='web'))
        item=response.get('data') or {}
        if response.get('success')!=1 or item.get('art_code')!=art or not item.get('notice_content'):
            raise ValueError(f'content part {index} missing')
        text.append(item['notice_content'])
    return dict(id=art,title=row['title'],date=publication_day(row),notice_date=(row.get('notice_date') or row.get('date'))[:10],published_at=row.get('display_time') or row.get('eiTime'),
                url=f'https://data.eastmoney.com/notices/detail/{code}/{art}.html',pdf=data.get('attach_url'),
                body=''.join(text),body_parts_total=parts,body_parts_read=len(text),
                body_complete=parts==len(text) and bool(text[0]))


def packet(client, stock, args):
    code, name = stock['SECURITY_CODE'], stock['SECURITY_NAME_ABBR']
    dest = client.root / 'companies' / (code + '.json')
    dest.parent.mkdir(exist_ok=True)
    if dest.exists() and not args.refresh:
        return json.loads(dest.read_text(encoding='utf-8'))
    result = dict(code=code, name=name, secucode=stock['SECUCODE'], errors=[], documents=[], financials=[], market={})
    try:
        rows, coverage = notices(client, code, args.since, args.as_of, args.max_pages)
        result['coverage'] = coverage
        result['announcement_titles'] = [{k:r.get(k) for k in ('title','notice_date','art_code','display_time','eiTime')} for r in rows]
        for row in select_documents(rows, args.documents):
            art = row['art_code']
            doc = dict(id=art, title=row['title'], date=row['notice_date'][:10], url=f'https://data.eastmoney.com/notices/detail/{code}/{art}.html')
            try:
                doc=document(client,code,row,args.content_parts)
                if not doc['body']:
                    result['errors'].append(art + ': empty body')
            except Exception as exc:
                doc['body'] = ''
                result['errors'].append(art + ': ' + str(exc))
            result['documents'].append(doc)
        value = client.get(FIN, dict(type='RPT_F10_FINANCE_MAINFINADATA', sty='APP_F10_MAINFINADATA',
                                     filter=f'(SECUCODE="{stock["SECUCODE"]}")', p=1, ps=20, sr=-1, st='REPORT_DATE', source='HSF10', client='PC'))
        records = (value.get('result') or {}).get('data')
        if not isinstance(records, list) or not records:
            raise ValueError('financial data unavailable')
        fields = ['REPORT_DATE','NOTICE_DATE','UPDATE_DATE','REPORT_TYPE','SECURITY_NAME_ABBR','PARENTNETPROFIT','KCFJCXSYJLR','TOTALOPERATEREVE','PARENTNETPROFITTZ','KCFJCXSYJLRTZ','MGJYXJJE','BPS','ZCFZL']
        result['financials'] = [{k:r.get(k) for k in fields} for r in records if str(r.get('NOTICE_DATE',''))[:10] <= args.as_of]
        if result['financials']:
            result['name'] = result['financials'][0]['SECURITY_NAME_ABBR']
        prefix = stock['SECUCODE'].split('.')[1].lower()
        if prefix in ('sh','sz','bj'):
            q = client.get('https://qt.gtimg.cn/q=' + prefix + code, text=True, encoding='gbk')
            bits = q.split('"')[1].split('~')
            if bits[2] != code:
                raise ValueError('quote identity mismatch')
            result['market'] = dict(name=bits[1], price=bits[3], time=bits[30])
            result['current_st'] = ordinary(bits[1])
    except Exception as exc:
        result['errors'].append(str(exc))
    dest.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
    return result


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--output', required=True)
    p.add_argument('--project')
    today=datetime.now(TZ).date()
    period=f'{today.year}-06-30' if today.month>=9 else (f'{today.year-1}-12-31' if today.month>=5 else f'{today.year-1}-06-30')
    p.add_argument('--period', default=period)
    p.add_argument('--as-of', default=datetime.now(TZ).date().isoformat())
    p.add_argument('--since', default='2020-01-01')
    p.add_argument('--max-pages', type=int, default=25)
    p.add_argument('--documents', type=int, default=24)
    p.add_argument('--content-parts', type=int, default=8, help='每份公告最多读取的正文接口分块数，每块最多5000字')
    p.add_argument('--workers', type=int, default=4)
    p.add_argument('--symbols')
    p.add_argument('--refresh', action='store_true',help='从本次归档响应重建公司材料；实时更新应使用新的输出目录')
    args = p.parse_args()
    if min(args.max_pages,args.documents,args.content_parts)<1 or not 1<=args.workers<=8:
        p.error('页数、材料数和正文分块数须为正；并发线程1至8')
    if args.since>args.as_of or args.as_of>today.isoformat():
        p.error('日期范围无效或晚于今天')
    for value in (args.since,args.as_of,args.period):
        datetime.strptime(value,'%Y-%m-%d')
    if not args.project:
        candidate=Path(__file__).resolve().parents[3] if len(Path(__file__).resolve().parents)>3 else None
        if candidate and (candidate/'src'/'stock_analysis_mcp').exists():args.project=str(candidate)
    client = Client(args.output, args.project)
    directory = universe(client, args.period)
    stocks = directory['candidates']
    try:
        current=current_universe(client)
        lookup={s['SECURITY_CODE']:s for s in stocks}
        stocks=[lookup.get(s['code'],dict(SECURITY_CODE=s['code'],SECURITY_NAME_ABBR=s['name'],SECUCODE=s['code']+('.SH' if s['market']==1 else '.SZ'))) for s in current['stocks']]
    except Exception as exc:
        directory['current_directory_error']=str(exc)
        print('当期ST板块名单不可用，降级财报目录，不能声称全市场覆盖: '+str(exc),flush=True)
    if args.symbols:
        codes = set(args.symbols.split(','))
        stocks = [r for r in stocks if r['SECURITY_CODE'] in codes]
    print(f'目录{directory["total"]}家，普通ST候选{len(stocks)}家', flush=True)
    index = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures = [pool.submit(packet, client, stock, args) for stock in stocks]
        for future in concurrent.futures.as_completed(futures):
            item = future.result()
            index.append({k:item.get(k) for k in ('code','name','current_st','errors','coverage')})
            print(f'[{len(index)}/{len(stocks)}] {item["code"]} {item["name"]}: {len(item["documents"])}正文，错误{len(item["errors"])}', flush=True)
    (client.root / 'collection.json').write_text(json.dumps(dict(as_of=args.as_of, fetched_at=datetime.now(TZ).isoformat(), companies=sorted(index,key=lambda r:r['code'])), ensure_ascii=False, indent=2), encoding='utf-8')


if __name__ == '__main__':
    main()
