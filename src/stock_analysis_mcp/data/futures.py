"""On-demand domestic futures; Sina source identities remain separate from stocks."""
from __future__ import annotations

import json
import re
from datetime import date, datetime
from zoneinfo import ZoneInfo

from .information import _cached, _request
from .assets import number, _result

DIRECTORY = 'https://vip.stock.finance.sina.com.cn/quotes_service/view/js/qihuohangqing.js'
QUOTES = 'https://vip.stock.finance.sina.com.cn/quotes_service/api/json_v2.php/Market_Center.getHQFuturesData'
BARS = 'https://stock2.finance.sina.com.cn/futures/api/jsonp.php/=/InnerFuturesNewService.'
HEADERS = {'Referer': 'https://finance.sina.com.cn/'}
EXCHANGES = ('shfe', 'ine', 'dce', 'czce', 'cffex', 'gfex')
INE_NODES = {'yy_qh', 'ehj_qh', 'lu_qh', 'bc_qh', 'ec_qh'}


def list_products(exchange=None, offline=False):
    if exchange is not None and exchange not in EXCHANGES:
        raise ValueError('unsupported domestic exchange')
    def load():
        from akshare.utils import demjson
        raw = _request(DIRECTORY, text=True, encoding='gb18030', extra_headers=HEADERS)
        obj = demjson.decode(raw[raw.index('{'):raw.index('}') + 1])
        rows = []
        for market, entries in obj.items():
            if market not in EXCHANGES:
                continue
            for item in entries[1:]:
                name, node = item[:2]
                actual = 'ine' if node in INE_NODES or '集运指数' in name else market
                rows.append({'exchange': actual, 'product': name, 'node': node,
                             'category': ('stock_index' if any(x in name for x in ('沪深', '上证', '中证')) else
                                          'treasury' if '国债' in name else 'commodity')})
        if not rows:
            raise ValueError('empty futures directory')
        return rows
    rows, meta = _cached('futures:v1:products', load, offline=offline, ttl=86400)
    return _result([r for r in rows if exchange is None or r['exchange'] == exchange], meta,
                   coverage='Sina published domestic products; not an exchange-completeness guarantee')


def get_quotes(node, page=1, page_size=100, offline=False):
    if not isinstance(node, str) or not re.fullmatch(r'[a-z0-9_]{1,40}', node):
        raise ValueError('use node from list_futures_products')
    if not 1 <= page <= 50 or not 1 <= page_size <= 100:
        raise ValueError('invalid paging')
    directory = list_products(offline=offline)
    product = next((r for r in directory['records'] if r['node'] == node), None)
    if product is None:
        return _result([], directory['source'], warnings=directory['warnings'] + ['品种未在来源目录中确认'])
    def load():
        raw = _request(QUOTES, params={'node': node, 'page': page, 'num': page_size,
                       'sort': 'position', 'asc': '0', 'base': 'futures'}, extra_headers=HEADERS)
        if not isinstance(raw, list):
            raise ValueError('invalid futures quotes response')
        rows = []
        mapping = {'trade': 'latest_price', 'settlement': 'settlement', 'presettlement': 'previous_settlement',
                   'open': 'open', 'high': 'high', 'low': 'low', 'volume': 'volume', 'position': 'open_interest',
                   'bidprice1': 'bid_price', 'askprice1': 'ask_price', 'bidvol1': 'bid_volume', 'askvol1': 'ask_volume'}
        for r in raw:
            symbol = str(r.get('symbol', '')).upper()
            if not re.fullmatch(r'[A-Z]{1,3}\d{3,4}', symbol):
                continue
            for key in ('settlement', 'presettlement'):
                if number(r.get(key)) == 0:
                    r[key] = None
            rows.append({**product, 'symbol': symbol, 'name': r.get('name'),
                         'source_time': r.get('ticktime'), 'source_date': r.get('date'),
                         **{out: number(r.get(key)) for key, out in mapping.items()}})
        return rows
    rows, meta = _cached(f'futures:v1:quotes:{node}:{page}:{page_size}', load, offline=offline, ttl=30)
    return _result(rows, meta, warnings=directory['warnings'], page=page, page_size=page_size,
                   has_more=len(rows) >= page_size, warnings_note='公开行情可能延迟；抓取时间不等于行情时间')


def get_kline(symbol, period='daily', start_date='2020-01-01', end_date=None, limit=320, offline=False):
    symbol = str(symbol).strip().upper()
    if not re.fullmatch(r'[A-Z]{1,3}(?:0|\d{3,4})', symbol):
        raise ValueError('期货代码应如 RB0、IF0、IF2612；不接受股票代码或期权代码')
    if period not in ('daily', '1', '5', '15', '30', '60') or not 1 <= limit <= 2000:
        raise ValueError('invalid period or limit')
    start = date.fromisoformat(start_date)
    end = date.fromisoformat(end_date) if end_date else datetime.now(ZoneInfo('Asia/Shanghai')).date()
    if start > end:
        raise ValueError('start_date must not exceed end_date')
    def load():
        method = 'getDailyKLine' if period == 'daily' else 'getFewMinLine'
        raw = _request(BARS + method, text=True, params={'symbol': symbol, 'type': period}, extra_headers=HEADERS)
        match = re.search(r'=\s*\((.*)\)\s*;', raw, re.S)
        data = json.loads(match.group(1)) if match else None
        if not isinstance(data, list) or not data:
            raise ValueError('来源未返回此期货代码的K线')
        rows = []
        for r in data:
            if number(r.get('s')) == 0:
                r['s'] = None
            stamp = str(r['d'])
            date.fromisoformat(stamp[:10])
            rows.append({'datetime': stamp, **{out: number(r.get(key)) for key, out in
                         {'o': 'open', 'h': 'high', 'l': 'low', 'c': 'close', 'v': 'volume',
                          'p': 'open_interest', 's': 'settlement'}.items()}})
        return sorted(rows, key=lambda r: r['datetime'])
    rows, meta = _cached(f'futures:v1:kline:{symbol}:{period}', load, offline=offline, ttl=300)
    selected = [r for r in rows if start.isoformat() <= r['datetime'][:10] <= end.isoformat()]
    warnings = ['仅返回来源可用历史，不保证日期区间完整；分钟线为有限近期窗口。',
                '夜盘时间保留来源标记，不据此推算交易日；成交量和持仓量沿用来源口径。']
    if symbol.endswith('0') and len(re.search(r'\d+$', symbol).group()) == 1:
        warnings.append('主力连续为来源拼接序列，换月可能跳空，不是可直接交易合约，不保证复权。')
    return _result(selected[-limit:], meta, symbol=symbol, period=period, timezone='Asia/Shanghai',
                   available_start=rows[0]['datetime'] if rows else None,
                   available_end=rows[-1]['datetime'] if rows else None, warnings=warnings)
