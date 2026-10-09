"""Public dividend snapshots; not a point-in-time corporate-action feed."""
import argparse
from datetime import date, datetime, timezone
import hashlib
import json
from pathlib import Path
import time
from urllib.parse import urlencode
from urllib.request import Request, urlopen

ENDPOINT = 'https://datacenter-web.eastmoney.com/api/data/v1/get'


def get_page(filter_text, page, size, timeout):
    query = dict(reportName='RPT_SHAREBONUS_DET', columns='ALL', pageSize=size,
        pageNumber=page, sortColumns='PLAN_NOTICE_DATE,SECURITY_CODE,REPORT_DATE',
        sortTypes='-1,1,-1', source='WEB', client='WEB', filter=filter_text)
    request = Request(ENDPOINT+'?'+urlencode(query), headers={'User-Agent':'Mozilla/5.0'})
    for attempt in range(2):
        try:
            with urlopen(request, timeout=timeout) as response:
                value = json.load(response)
            if value.get('success') is not True or not isinstance(value.get('result'), dict):
                raise RuntimeError('Provider rejected query: '+str(value.get('message')))
            return value['result']
        except Exception:
            if attempt:
                raise
            time.sleep(1)


def fetch(filter_text, max_pages=40, timeout=20):
    first = get_page(filter_text, 1, 500, timeout)
    pages, count = int(first['pages']), int(first['count'])
    if pages > max_pages:
        raise ValueError(f'Query requires {pages} pages, limit is {max_pages}; narrow query or explicitly raise limit')
    rows = list(first.get('data') or [])
    for page in range(2, pages+1):
        time.sleep(.2)
        result = get_page(filter_text, page, 500, timeout)
        if int(result['pages']) != pages or int(result['count']) != count:
            raise RuntimeError('Provider coverage changed during pagination; rerun a narrower query')
        rows.extend(result.get('data') or [])
    if len(rows) != count:
        raise RuntimeError(f'Incomplete pagination: expected {count}, received {len(rows)}')
    if len({json.dumps(r, sort_keys=True, ensure_ascii=False) for r in rows}) != len(rows):
        raise RuntimeError('Duplicate pagination rows; do not claim complete coverage')
    return rows, dict(pages=pages, provider_count=count, received=len(rows))


def day(value):
    if value is None or value == '':
        return None
    result = str(value)[:10]
    date.fromisoformat(result)
    return result


def normalize(raw, as_of):
    required = {'SECUCODE','SECURITY_CODE','SECURITY_NAME_ABBR','REPORT_DATE','ASSIGN_PROGRESS'}
    if not required.issubset(raw):
        raise ValueError('Dividend schema changed; missing required fields')
    ex_date = day(raw.get('EX_DIVIDEND_DATE'))
    notice = day(raw.get('NOTICE_DATE'))
    progress = raw['ASSIGN_PROGRESS'] or ''
    cancelled = any(word in progress for word in ('取消','终止','不分配','不分红'))
    timing = ('cancelled_or_no_distribution' if cancelled else
        'past_ex_date' if progress == '实施分配' and ex_date and ex_date <= as_of else
        'upcoming_ex_date' if progress == '实施分配' and ex_date and ex_date > as_of else
        'proposal_or_dates_unconfirmed')
    return dict(security_id=raw['SECUCODE'], symbol=raw['SECURITY_CODE'], name=raw['SECURITY_NAME_ABBR'],
        report_period=day(raw['REPORT_DATE']), proposal_date=day(raw.get('PLAN_NOTICE_DATE')),
        latest_notice_date=notice, record_date=day(raw.get('EQUITY_RECORD_DATE')),
        ex_date=ex_date, pay_date=None, implementation_notice_date=None,
        progress=progress, timing=timing, plan_text=raw.get('IMPL_PLAN_PROFILE'),
        cash_raw=raw.get('PRETAX_BONUS_RMB'), cash_unit='unverified; check plan text',
        bonus_raw=raw.get('BONUS_RATIO'), transfer_raw=raw.get('IT_RATIO'),
        source_url='https://data.eastmoney.com/yjfp/detail/'+raw['SECURITY_CODE']+'.html',
        official_announcement_verified=False,
        notice_after_as_of=notice > as_of if notice else None)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument('--period', action='append', help='Repeat YYYY0630 or YYYY1231')
    group.add_argument('--symbol', help='Six-digit source code; output retains provider exchange ID')
    parser.add_argument('--as-of', required=True, help='YYYY-MM-DD: date classification only, not historical reconstruction')
    parser.add_argument('--output', required=True)
    parser.add_argument('--max-pages', type=int, default=40)
    parser.add_argument('--timeout', type=float, default=20)
    args = parser.parse_args()
    try:
        if date.fromisoformat(args.as_of).isoformat() != args.as_of:
            raise ValueError('Invalid as-of date')
        if args.max_pages < 1 or args.timeout <= 0:
            raise ValueError('Require positive timeout and page limit')
        if args.symbol:
            if len(args.symbol)!=6 or not args.symbol.isascii() or not args.symbol.isdigit():
                raise ValueError('Require a six-digit code')
            filters = [f'(SECURITY_CODE="{args.symbol}")']
        else:
            filters=[]
            for value in dict.fromkeys(args.period):
                if len(value)!=8 or not value.isascii() or not value.isdigit() or value[4:] not in ('0630','1231'):
                    raise ValueError('Report periods must be YYYY0630 or YYYY1231')
                iso = date.fromisoformat(f'{value[:4]}-{value[4:6]}-{value[6:]}').isoformat()
                filters.append(f"(REPORT_DATE='{iso}')")
    except ValueError as exc:
        parser.error(str(exc))
    output = Path(args.output).expanduser().resolve()
    if output.exists():
        raise FileExistsError('Use a new dated output path to retain snapshots: '+str(output))
    rows, metadata = [], []
    for filter_text in filters:
        received, coverage = fetch(filter_text, args.max_pages, args.timeout)
        rows.extend(received)
        metadata.append(dict(filter=filter_text, **coverage))
    normalized = [normalize(r, args.as_of) for r in rows]
    normalized.sort(key=lambda r:(r['latest_notice_date'] or '',r['security_id'],r['report_period']), reverse=True)
    raw_json = json.dumps(rows, ensure_ascii=False, sort_keys=True, allow_nan=False)
    result = dict(fetched_at=datetime.now(timezone.utc).isoformat(), as_of=args.as_of, source=ENDPOINT,
        coverage=metadata, full_a_share_coverage_verified=False, historical_point_in_time_verified=False,
        limitations=['Current revised snapshot; as_of classifies ex-date timing, not publication visibility.',
            'No verified A-share universe join; no-match does not mean no dividend.',
            'Pay date and implementation notice date require another source or formal announcement.',
            'Cash/share units require verification; past ex-date does not confirm cash receipt.'],
        raw_sha256=hashlib.sha256(raw_json.encode('utf-8')).hexdigest(), records=normalized, raw_records=rows)
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open('x', encoding='utf-8') as target:
        target.write(json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False)+'\n')
    print(json.dumps(dict(records=len(rows), symbols=len({r['security_id'] for r in normalized}),
        coverage_verified=False, output=str(output)), ensure_ascii=False))


if __name__ == '__main__':
    main()
