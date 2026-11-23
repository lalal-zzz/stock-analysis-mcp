import json
import pytest
from stock_analysis_mcp.data import futures


@pytest.fixture(autouse=True)
def cache(tmp_path, monkeypatch):
    monkeypatch.setenv('STOCK_ANALYSIS_DATA_DIR', str(tmp_path))


def test_bars_mapping_and_continuous_warning(monkeypatch):
    monkeypatch.setattr(futures, '_request', lambda *a, **k:
        '/* comment */=([{"d":"2026-11-20","o":"10","h":"12","l":"9","c":"11","v":"4","p":"6","s":"0"}]);')
    r = futures.get_kline('IF0', start_date='2026-11-01')
    assert r['records'][0]['close'] == 11
    assert r['records'][0]['open_interest'] == 6
    assert r['records'][0]['settlement'] is None
    assert len(r['warnings']) == 3
    monkeypatch.setattr(futures, '_request', lambda *a, **k: pytest.fail('offline network'))
    assert futures.get_kline('IF0', offline=True)['records'] == r['records']


@pytest.mark.parametrize('symbol', ['600000', 'IF2612-C-4000', '../IF0', 'IF'])
def test_invalid_codes(symbol):
    with pytest.raises(ValueError):
        futures.get_kline(symbol)


def test_directory_and_quotes(monkeypatch):
    def request(url, **kwargs):
        if url == futures.DIRECTORY:
            return "X={shfe:['SH',['crude','yy_qh']],cffex:['CF',['index','qz_qh']]}"
        return [{'symbol': 'IF2612', 'trade': '4000', 'position': '10', 'settlement': '0'}]
    monkeypatch.setattr(futures, '_request', request)
    assert futures.list_products('ine')['records'][0]['node'] == 'yy_qh'
    r = futures.get_quotes('qz_qh')
    assert r['records'][0]['exchange'] == 'cffex'
    assert r['records'][0]['open_interest'] == 10
    assert r['records'][0]['settlement'] is None
    assert futures.get_quotes('unknown')['records'] == []


async def test_registration_and_validation():
    from stock_analysis_mcp import server
    names = {t.name for t in await server.list_tools()}
    assert {'list_futures_products', 'get_futures_quotes', 'get_futures_kline'} <= names
    r = json.loads((await server.call_tool('get_futures_kline', {'symbol': 'IF0', 'limit': 2001}))[0].text)
    assert r['error']


def test_empty_or_malformed_source(monkeypatch):
    monkeypatch.setattr(futures, '_request', lambda *a, **k: '=(null);')
    r = futures.get_kline('RB0')
    assert not r['records']
    assert r['source']['status'] == 'unavailable'
