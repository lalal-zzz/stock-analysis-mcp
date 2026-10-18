"""Build conditional ST-removal research from archived evidence, not stock-price predictions."""
import argparse
import csv
import html
import json
from collections import Counter
from datetime import date, timedelta
from pathlib import Path
import re
from st_removal_rules import attach_rules, disclosed_stage, SOURCES

DATE = r'(20\d{2})年(\d{1,2})月(\d{1,2})日'
RULES = {
 '处罚/虚假记载': '完成处罚涉及年度追溯重述；正式处罚决定作出满12个月；索赔预计负债等适用要求；不存在其他仍有效的风险警示',
 '内控非标': '内控缺陷整改且有效运行，取得最近会计年度符合要求的内控审计报告；其他戴帽事项全部消除',
 '资金占用': '占用资金实际清偿/消除并取得会计师专项核查意见；不能只看还款承诺',
 '违规担保': '违规担保责任实际解除/消除并取得法律意见等必要文件',
 '账户冻结': '主要银行账户冻结情形消除，账户及经营恢复；核实是否存在其他戴帽原因',
 '经营/持续经营': '严重经营影响消除；若因连续亏损与持续经营不确定性戴帽，核对最近年度审计及对应指标/持续经营意见',
 '分红不达标': '核对适用板块规则、最近年度经审计利润及获股东会通过的现金分红方案',
}


def compact(text):
    return re.sub(r'\s+', '', text or '')


def snippet(text, pattern, before=60, after=220):
    m = re.search(pattern, text)
    return text[max(0,m.start()-before):m.end()+after] if m else ''


def source(doc, quote):
    return dict(date=doc['date'], title=doc['title'], url=doc['url'], quote=quote)


def match_evidence(docs, pattern, limit=2):
    out = []
    for doc in sorted(docs,key=lambda d:d['date'],reverse=True):
        text = compact(doc.get('body'))
        s = snippet(text, pattern)
        if s:
            out.append(source(doc, s))
        if len(out) >= limit:
            break
    return out


def iso(m):
    try:
        return date(*map(int, m.groups())).isoformat()
    except ValueError:
        return None


def plus_year(value):
    dt = date.fromisoformat(value)
    try:
        return dt.replace(year=dt.year + 1).isoformat()
    except ValueError:
        return dt.replace(year=dt.year + 1, day=28).isoformat()


def analyse(packet, as_of):
    audit_window_year = int(as_of[:4]) + (1 if int(as_of[5:7]) >= 5 else 0)
    audit_fiscal_year = audit_window_year - 1
    docs = packet.get('documents', [])
    risks = [d for d in docs if re.search(r'风险警示|摘帽', d['title'])]
    origins = [d for d in risks if re.search(r'(?:实施|被实施).{0,12}风险警示.{0,12}(?:停牌|停复牌)',d['title']) and '叠加' not in d['title']]
    origin_cutoff = max((d['date'] for d in origins),default='')
    # Only risk-specific documents identify ST triggers; annual reports mention many unrelated historic issues.
    reason_docs = []
    for doc in risks:
        if origin_cutoff and doc['date'] < origin_cutoff:
            continue
        text = compact(doc.get('body'))
        start = re.search(r'实施.{0,8}风险警示的(?:适用情形|原因)|被实施.{0,8}风险警示的原因', text)
        section = text[start.start():start.start()+2200] if start else text[:2400]
        reason_docs.append((doc, section))
    reasons, reason_evidence = [], []
    patterns = {
      '处罚/虚假记载': r'(?:行政处罚事先告知书|行政处罚决定书).{0,650}(?:虚假记载|财务造假)|(?:年度报告|财务指标).{0,25}存在虚假记载',
      '内控非标': r'(?:内部控制|内控).{0,100}(?:否定意见|无法表示意见)|(?:否定意见|无法表示意见).{0,80}内部控制',
      '资金占用': r'(?:资金占用|占用资金).{0,220}(?:触及|风险警示)|(?:触及|风险警示).{0,160}(?:资金占用|占用资金)',
      '违规担保': r'(?:违规担保|违反规定程序对外担保).{0,200}(?:触及|风险警示)',
      '账户冻结': r'(?:主要银行账[户号]|主要账[户号]).{0,50}(?:冻结|被冻结)',
      '经营/持续经营': r'(?:连续|最近)[三3]个会计年度.{0,220}持续经营|生产经营活动受到严重影响',
      '分红不达标': r'(?:分红|现金分配).{0,100}(?:低于|不符合).{0,160}风险警示',
    }
    # Explicit historical penalty often discusses former occupation. Keep this as a historical issue,
    # not a separate active funding-occupation trigger unless an actual risk doc clearly says so.
    for key, pattern in patterns.items():
        for doc, text in reason_docs:
            s = snippet(text, pattern, 30, 100)
            if s:
                reasons.append(key)
                reason_evidence.append(source(doc,s))
                break
    if '处罚/虚假记载' in reasons and '资金占用' in reasons:
        occupation_docs = [x for x in reason_docs if re.search(r'9\.8\.1条.{0,25}第[（(]?[一1][）)]?项', x[1])]
        if not occupation_docs:
            reasons.remove('资金占用')
    onset = []
    original = [d for d in risks if re.search(r'(?:实施|被实施).{0,12}风险警示.{0,12}(?:停牌|停复牌)',d['title']) and '叠加' not in d['title']]
    onset_docs = sorted(original,key=lambda d:d['date'],reverse=True)[:1] or risks
    for doc in onset_docs:
        text = compact(doc.get('body'))
        pats = [r'(?:(?:实施|实行).{0,10}起始日|风险警示起始日)(?:期)?[：:为]*'+DATE,
                r'自'+DATE+r'(?:[（(][^）)]{0,8}[）)])?(?:起|开市起|复牌起).{0,20}(?:实施其他风险警示|被实施其他风险警示)',
                r'于'+DATE+r'(?:[（(][^）)]{0,8}[）)])?(?:起|开市起|复牌起).{0,15}(?:实施其他风险警示|被实施其他风险警示)']
        for pat in pats:
            m = re.search(pat,text)
            if m and iso(m) and iso(m) <= as_of:
                onset.append(dict(date=iso(m), source=doc['url'], quote=snippet(text,pat)))
    # Retain all dates, select earliest within current collected ST episode; reviewed overrides can correct transitions.
    onset.sort(key=lambda x:x['date'])
    penalties = []
    for doc in docs:
        if not re.search(r'行政处罚决定|整改|风险警示',doc['title']):
            continue
        text = compact(doc.get('body'))
        # First-hand receipt is a weaker date anchor than explicit decision issuance.
        for kind, pat in [('作出',r'(?:于|在)'+DATE+r'.{0,25}作出.{0,50}行政处罚决定书'),
                          ('作出',r'行政处罚决定书.{0,30}(?:作出日期|作出时间)[：:]?'+DATE),
                          ('作出',r'作出[《〈]?行政处罚决定书[》〉]?(?:[（(][^）)]{0,35}[）)])?(?:之日|的日期|日期)[（(：:]{0,3}(?:即|为)?'+DATE),
                          ('收到',r'(?:于|在)?'+DATE+r'[^。；]{0,25}?收到[^。；]{0,100}?行政处罚决定书')]:
            for m in re.finditer(pat,text):
                dt = iso(m)
                quote = text[max(0,m.start()-30):m.end()+100]
                if kind=='收到':
                    received_at=text.find('收到',m.start(),m.end())
                    if re.search(r'披露|发布|公告|关于',text[m.end(3)+1:received_at]):
                        continue
                if dt and dt <= as_of and '事先告知书' not in text[m.start():m.end()]:
                    penalties.append(dict(date=dt, basis=kind, anniversary=plus_year(dt), source=doc['url'], quote=quote))
    penalties.sort(key=lambda x:(x['date'], x['basis']=='作出'),reverse=True)
    anchor = penalties[0] if penalties else None
    if anchor and origin_cutoff and anchor['source'] not in [d['url'] for d in docs if d['date'] >= origin_cutoff]:
        anchor = None
    progress = {
      '整改声明': match_evidence(docs, r'现已全部整改完毕|相关整改措施已落实完毕|已完成(?:全部)?整改|整改工作已完成'),
      '追溯落实': match_evidence(docs, r'公司于'+DATE+r'.{0,250}审议通过.{0,120}(?:差错更正|追溯调整)|已(?:经)?完成.{0,35}(?:会计差错更正|追溯重述|追溯调整)'),
      '内控有效意见': match_evidence(sorted([d for d in docs if '内部控制审计报告' in d['title']],key=lambda d:d['date'],reverse=True)[:1], r'我们认为.{0,130}(?:保持了有效|内部控制是有效)|标准无保留意见'),
      '占用清偿声明': match_evidence(risks, r'占用(?:资金)?(?:本金及利息|本息|款项)?.{0,40}(?:已全部归还|已全部清偿|已全部偿还)|不存在非经营性资金占用|资金占用余额为[零0]'),
      '未解决线索': match_evidence(risks[:3], r'(?:尚未|仍未|未能).{0,45}(?:清偿|归还|解决|消除|解除|整改)|(?:仍有|尚有).{0,30}(?:资金占用|违规担保)'),
    }
    financials = sorted(packet.get('financials',[]),key=lambda r:r['REPORT_DATE'],reverse=True)
    annuals = [r for r in financials if str(r['REPORT_DATE'])[5:10]=='12-31'][:3]
    latest = financials[0] if financials else None
    def profit_text(row):
        def fmt(x):
            return '缺失' if x is None else f'{x/1e8:.2f}'
        return f'{row["REPORT_DATE"][:10]}归母{fmt(row.get("PARENTNETPROFIT"))}亿/扣非{fmt(row.get("KCFJCXSYJLR"))}亿'
    finance = '；'.join(profit_text(r) for r in annuals)
    if latest and latest not in annuals:
        finance += '；' + profit_text(latest)
    all_titles = packet.get('announcement_titles',[])
    latest_origin = max((d['date'] for d in risks if re.search(r'实施.*风险警示.*停|叠加.*风险警示',d['title'])),default='')
    removal_titles = [r for r in all_titles if '撤销' in r['title'] and '风险警示' in r['title'] and r['notice_date'][:10] >= latest_origin]
    full = [r for r in removal_titles if '部分' not in r['title'] and '继续' not in r['title'] and '退市风险警示' not in r['title']]
    applied = next((r for r in full if '申请' in r['title']),None)
    approved = next((r for r in full if '申请' not in r['title'] and '停' in r['title']),None)
    achieved, pending = [], []
    if progress['整改声明']:
        achieved.append('公司已披露整改完成（声明，不代替审计）')
    if progress['追溯落实']:
        achieved.append('检索到追溯更正落实证据，需核对覆盖处罚全部年度')
    if progress['内控有效意见']:
        achieved.append('检索到内控有效/无保留意见，须核对年度及是否有更新非标')
    if progress['占用清偿声明']:
        achieved.append('检索到占用清偿/不存在占用声明，须核对最新余额')
    if anchor and '处罚/虚假记载' in reasons:
        if anchor['anniversary'] <= as_of and anchor['basis']=='作出':
            achieved.append('正式决定满12个月')
        else:
            pending.append('处罚时间门槛'+('待到期' if anchor['anniversary']>as_of else '待核对作出日期'))
    if not reasons:
        pending.append('戴帽实际原因未在所取正文中可靠识别，需人工补查')
    if '处罚/虚假记载' in reasons and not progress['追溯落实']:
        pending.append('处罚涉及年度追溯重述及专项核查落实待核实')
    if '内控非标' in reasons:
        pending.append('最近年度合格内控审计及持续有效运行待逐项确认')
    if '资金占用' in reasons:
        pending.append('最新占用余额及会计师专项核查待确认')
    if '违规担保' in reasons:
        pending.append('担保责任解除及法律意见待确认')
    if '账户冻结' in reasons:
        pending.append('主要账户解冻范围及经营恢复待确认')
    if '经营/持续经营' in reasons:
        pending.append('下一年度审计与持续经营意见待确认；中期盈利不能替代')
    pending.append('不存在其他仍有效的风险警示，最终经交易所审核')
    window = '无法可靠估计；等待整改、专项核查或审计证据'
    level = '待补证据'
    reasoning = '信息尚不足，不能仅凭盈利或股吧预期认定将摘帽。'
    if '处罚/虚假记载' in reasons:
        level = '处罚满期观察'
        if anchor:
            window = anchor['anniversary'] + '前后关注申请条件；审核通过时间另计'
            if anchor['basis']=='收到':
                window += '（按收件日期估计，正式作出日期待核）'
            delta = (date.fromisoformat(anchor['anniversary'])-date.fromisoformat(as_of)).days
            if 0 <= delta <= 90 and (progress['整改声明'] or progress['追溯落实']):
                level = '未来90日条件型候选'
            reasoning = '处罚类存在可计算的等待期限；'+('已发现整改/追溯线索，时间门槛解除后可能推进。' if progress['整改声明'] or progress['追溯落实'] else '仍需落实追溯重述，不能只按满周年安排买入。')
        else:
            window = '正式处罚决定日期待核；事先告知书日期不能起算12个月'
    if '内控非标' in reasons and not progress['内控有效意见']:
        window = f'通常关注{audit_fiscal_year}年报及内控审计披露窗口（{audit_window_year}年春）；不代表届时必然完成'
        level = '等待年度审计验证'
        reasoning = '盈利改善不足以消除内控非标；合格内控审计是关键，整改声明只能提高跟踪价值。'
    if any(r in reasons for r in ['资金占用','违规担保','账户冻结']):
        level = '实质障碍待消除'
        window = '取决于实际清偿/解冻/解除责任及核查，无法仅按年报日期推算'
        reasoning = '能否摘帽取决于债务、占用或账户问题是否实际解决；承诺、方案和盈利均不能替代完成。'
    if '经营/持续经营' in reasons and len(reasons)==1:
        level = '经营改善观察'
        window = f'关注{audit_fiscal_year}年报审计（{audit_window_year}年春）及持续经营意见，仍可能延期'
        reasoning = '需从实际经营及审计意见判断改善是否足以消除戴帽原因，不能套用连续三年盈利。'
    if len(reasons)>1:
        reasoning += '识别到多类历史/现行事项，必须核对哪些仍有效，不能只消除一项。'
    if applied:
        level = '已申请（非提前预期池）'
        reasoning = '已有申请公告，不属于尚未申请的提前研究对象；仍须核对是否有更新障碍。'
    if approved or packet.get('current_st') is False:
        level = '已退出普通ST/状态变化'
        reasoning = '已获批撤销或最新行情简称已不是普通ST，不纳入未来普通ST摘帽候选。'
    if packet.get('errors'):
        reasoning += '存在抓取缺口，结论置信度下调。'
    return dict(code=packet['code'],name=packet['name'],current_st=packet.get('current_st'),
                onset=onset, onset_display=onset[0]['date'] if onset else '未可靠提取，需查本轮实施/转ST公告',
                reasons=reasons,reason_evidence=reason_evidence,conditions=[RULES[r] for r in reasons],
                penalty_anchor=anchor,progress=progress,achieved=achieved,pending=pending,
                financial_summary=finance,latest_financial=latest,latest_annual=annuals[0] if annuals else None,level=level,window=window,reasoning=reasoning,
                documents=[{k:d.get(k) for k in ('id','date','notice_date','published_at','title','url','body_complete','body_parts_total','body_parts_read')} for d in docs],
                errors=packet.get('errors',[]),coverage=packet.get('coverage',{}),market=packet.get('market',{}),
                freshness_check=packet.get('freshness_check'),
                review_status='规则初筛，原文摘录支持；尚未研究者逐项确认全部条件')


def presentation(row, as_of):
    applied = row['level'] == '已申请（非提前预期池）'
    state = row.get('readiness')
    approved = row['level'] in ('已获准待生效','已生效撤销','已退出普通ST/状态变化')
    if approved:
        state, color, tier = row.get('latest_status','已退出普通ST/状态变化'), 'slate', '已确定事件，移出预期池'
    elif applied:
        state, color, tier = '已公告申请，等待审核', 'purple', '申请后观察'
    elif state == '整改证据较充分，主要等期限':
        color, tier = 'green', 'A 重点核验'
    elif state == '实质问题已披露解决，待专项核查':
        color, tier = 'teal', 'A 重点核验'
    elif row['level'] in ('未来90日条件型候选', '处罚满期观察'):
        state, color, tier = '处罚期限可观察，关键材料仍待核', 'amber', 'B 条件观察'
    elif row['level'] in ('等待年度审计验证', '经营改善观察'):
        state, color, tier = '等待审计或经营验证', 'blue', 'C 中期跟踪'
    else:
        state, color, tier = '实质障碍或证据缺口', 'red', 'D 暂缓前排'
    def loss_label(fin):
        if not fin:
            return '数据缺失'
        net, core = fin.get('PARENTNETPROFIT'), fin.get('KCFJCXSYJLR')
        def label(v):
            return '缺失' if v is None else (('亏损' if v < 0 else '盈利' if v > 0 else '持平') + f' {v/1e8:.2f}亿')
        return f"{str(fin['REPORT_DATE'])[:10]}：归母{label(net)} / 扣非{label(core)}"
    latest = row.get('latest_financial') or {}
    annual = row.get('latest_annual') or {}
    values = [latest.get('PARENTNETPROFIT'), latest.get('KCFJCXSYJLR')]
    latest_loss = any(v is not None and v < 0 for v in values)
    annual_loss = any(annual.get(k) is not None and annual[k] < 0 for k in ('PARENTNETPROFIT','KCFJCXSYJLR'))
    row['loss_filter'] = 'loss' if latest_loss else ('profit' if all(v is not None and v >= 0 for v in values) else 'unknown')
    row['latest_profit_status'] = loss_label(latest)
    row['annual_profit_status'] = loss_label(annual)
    row['profit_gate'] = '未通过：最新一期归母或扣非亏损' if latest_loss else ('待核：数据不全' if row['loss_filter']=='unknown' else '通过：最新一期归母及扣非均不亏损')
    loss = latest_loss or annual_loss
    row['any_loss'] = loss
    if loss:
        row['profit_warning'] = ('经营风险：最新期或最近年度归母/扣非亏损；摘帽不等于主营改善。'
            '需排查营收、净资产、审计及现金流等新增警示/退市风险，不能仅凭亏损自动推定*ST。')
    else:
        row['profit_warning'] = '暂无所取两期归母/扣非亏损，不代表现金流、审计或其他风险已经消除。' if row['loss_filter']=='profit' else '财务数据不全，不能确认不亏损。'
    if row.get('specific_risk'):
        row['profit_warning'] += row['specific_risk']
    row['event_vs_profit'] = ('本轮原因含持续经营，须以最近年度审计确认盈利或不确定性消除路径；半年结果不能替代。'
        if row.get('operating_subtype')=='连续亏损并持续经营不确定性' else
        '当前匹配原因不以本期盈利作为统一门槛；可能带亏损摘帽，但经营风险独立存在。')
    start = row.get('window_start')
    basis = row.get('date_basis', '')
    if not start and color == 'amber':
        anchor = row.get('penalty_anchor')
        if anchor:
            start = anchor['anniversary']
            basis = '作出日周年' if anchor['basis'] == '作出' else '收件周年估计；作出日待核'
    if not start:
        match = re.search(r'(20\d{2})年(\d{1,2})月', row['window'])
        if match:
            start = f'{match[1]}-{int(match[2]):02d}-01'
            basis = '情景月份，非确定日期'
    if not start and color == 'blue' and ('年报' in row['window'] or '审计' in row['window']):
        audit_year = int(as_of[:4]) + (1 if int(as_of[5:7]) >= 5 else 0)
        start, basis = f'{audit_year}-03-01', f'{audit_year-1}年报披露季情景（{audit_year}年3—4月）；非确认进度'
    expired = bool(start and start < as_of)
    if expired:
        start, basis = None, '原期限已过，尚未确认申请；须查明剩余障碍'
    if applied:
        evidence = next((d for d in row['documents'] if '申请' in d['title'] and '撤销' in d['title'] and '部分' not in d['title'] and '进展' not in d['title'] and '退市风险' not in d['title']), None)
        row['application_evidence'] = evidence
        if evidence:
            row['catalyst'] = evidence['date']+'申请公告；等待交易所审核及补充材料进展'
        start, basis = None, '已申请；实际审核结束时间未确定'
    if approved:
        start, basis = row.get('effective_date'), '公告已确定的生效日；不是预测窗口'
    row.update(readiness=state, color=color, priority_tier=tier, window_start=start,
               date_basis=basis or '进度驱动，无法可靠定日',
               catalyst=row.get('catalyst') or '；'.join(row['pending'][:2]),
               pool='approved' if approved else 'applied' if applied else 'expected')
    row['sort_date'] = start or '9999-12-31'
    row['sort_priority'] = {'green':0,'teal':1,'amber':2,'blue':3,'red':4,'purple':5,'slate':6}[color]
    return row


def render(root, as_of, overrides=None):
    packets = {p.stem:json.loads(p.read_text(encoding='utf-8')) for p in sorted((root/'companies').glob('*.json'))}
    reviewed = json.loads(Path(overrides).read_text(encoding='utf-8')) if overrides and Path(overrides).exists() else {}
    rows=[]
    for code, packet in packets.items():
        row=analyse(packet,as_of)
        row.update(reviewed.get(code,{}))
        if 'conditions' not in reviewed.get(code,{}):
            row['conditions']=[RULES[r] for r in row['reasons'] if r in RULES]
        # Newly disclosed full removal decisions outrank stale manual application labels.
        row.update(disclosed_stage(packet,as_of,row['onset_display']))
        attach_rules(row)
        presentation(row,as_of)
        rows.append(row)
    pool_order={'expected':0,'applied':1,'approved':2}
    rows.sort(key=lambda r:(pool_order[r['pool']],r['sort_date'],r['sort_priority'],r['code']))
    for i,row in enumerate(rows,1):row['display_rank']=i
    universe=json.loads((root/'universe.json').read_text(encoding='utf-8'))
    current=json.loads((root/'universe_current.json').read_text(encoding='utf-8')) if (root/'universe_current.json').exists() else None
    freshness=json.loads((root/'freshness.json').read_text(encoding='utf-8')) if (root/'freshness.json').exists() else {}
    missing=sorted({s['code'] for s in current['stocks']}-{r['code'] for r in rows}) if current else None
    result=dict(as_of=as_of,company_count=len(rows),reviewed_count=len(reviewed),universe=universe,current_universe=current,
                missing_current=missing,categories=dict(Counter(r['level'] for r in rows)),pools=dict(Counter(r['pool'] for r in rows)),
                freshness=freshness,rule_sources=SOURCES,companies=rows)
    (root/'research.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    fields={'display_rank':'时间序号','code':'代码','name':'公司','board':'板块','onset_display':'本轮ST时间','reasons':'有效/申请所涉戴帽原因',
            'readiness':'事件状态','pool':'事件分组','rule_route':'对应规则与撤销条件','profit_relation':'盈利与规则的关系',
            'removal_thesis':'为何可能摘帽或申请依据','remaining_barriers':'尚缺条件/审核障碍',
            'latest_profit_status':'最新一期盈亏','annual_profit_status':'最近年度盈亏','profit_gate':'投资不亏损筛选（非法定统一门槛）',
            'profit_warning':'独立经营风险','event_vs_profit':'亏损与摘帽的关系','window_start':'排序窗口','date_basis':'日期性质',
            'window':'完整时间窗口','catalyst':'下一催化','achieved':'已有证据','pending':'尚待核实','reasoning':'逐股研究判断','review_status':'核验范围'}
    with (root/'ST摘帽预期研究表.csv').open('w',encoding='utf-8-sig',newline='') as f:
        w=csv.writer(f);w.writerow(list(fields.values())+['规则来源','公告来源'])
        for row in rows:
            w.writerow(['；'.join(row[k]) if isinstance(row[k],list) else row.get(k) for k in fields]+
                       [row['rule_source'],'；'.join(d['url'] for d in row['documents']+row.get('additional_sources',[]))])
    def esc(x):return html.escape(str(x or ''),quote=True)
    def source_link(d,label=None):return f'<a href="{esc(d["url"])}">{esc(label or d["title"])} ↗</a>'
    def company_table(items,table_id):
        body=[]
        for row in items:
            trigger=row.get('trigger_detail') or ('；'.join(row['reasons'])+('；'+row['operating_subtype'] if row.get('operating_subtype') else ''))
            route=''.join(f'<p><strong>{esc(r["reason"])} · {esc(r["clause"])}</strong><br>{esc(r["condition"])}</p>' for r in row['rule_analysis'])
            latest=sorted([d for d in row['documents'] if '风险警示' in d['title']],key=lambda d:d['date'],reverse=True)
            stage=row.get('status_evidence') or row.get('application_evidence') or (latest[0] if latest else None)
            link=source_link(stage,'状态依据公告') if stage else '缺少可确认的状态公告'
            extra=''.join(f'<li>{esc(d["date"])} {source_link(d)}</li>' for d in row['documents']+row.get('additional_sources',[]))
            ev='；'.join(row['achieved']) or '尚缺已消除证据'
            attrs=f'data-level="{esc(row["level"])}" data-code="{row["code"]}" data-pool="{row["pool"]}" data-date="{row["sort_date"]}" data-priority="{row["sort_priority"]}" data-loss="{row["loss_filter"]}"'
            body.append(f'''<tr {attrs}>
<td class="rank">{row['display_rank']}</td>
<td class="identity"><strong>{esc(row['name'])}</strong><span class="sub">{row['code']} · {esc(row['board'])}</span><div class="onset"><b>本轮ST时间</b><br>{esc(row['onset_display'])}</div></td>
<td><strong>{esc(trigger)}</strong></td>
<td><span class="status {row['color']}">{esc(row['readiness'])}</span><span class="sub">{esc(row['priority_tier'])}</span><p>{esc(ev)}</p></td>
<td>{route}{source_link({'url':row['rule_source'],'title':'交易所现行规则'})}<span class="sub">{esc(row['profit_relation'])}</span></td>
<td><p>{esc(row['removal_thesis'])}</p><span class="sub">{esc(row['reasoning'])}</span></td>
<td><strong class="date">{esc(row.get('effective_date') or row['window_start'] or '日期待定')}</strong><span class="sub">{esc(row['date_basis'])}</span><p>{esc(row['window'])}</p></td>
<td><strong>{esc(row['latest_profit_status'])}</strong><span class="sub">{esc(row['annual_profit_status'])}</span><p class="risk {'loss' if row['any_loss'] else ''}">{esc(row['profit_warning'])}</p><span class="sub">{esc(row['event_vs_profit'])}</span></td>
<td><p>{esc(row['remaining_barriers'])}</p><p class="catalyst">下一催化：{esc(row['catalyst'])}</p>{link}<span class="sub">公开时间：{esc((stage or {}).get('published_at') or (stage or {}).get('date') or '待核')}<br>正式公告日：{esc((stage or {}).get('notice_date') or '见原文')}</span><details><summary>全部原文、财务与核验范围</summary><p>{esc(row['financial_summary'])}</p><p>{esc(row['review_status'])}</p><p>历史材料缺口：{esc(row['errors'])}</p><ul>{extra}</ul></details></td>
</tr>''')
        headings=['序号','公司 / 本轮ST时间','实际戴帽原因','事件状态 / 已有证据','对应规则与撤销条件','为何可能摘帽 / 申请依据','条件窗口 / 已确定生效日','最新及年度盈亏 / 经营风险','尚缺条件 / 下一催化 / 公告']
        widths=[50,180,250,235,330,340,230,310,340]
        return '<div class="table-wrap"><table id="'+table_id+'"><colgroup>'+''.join(f'<col style="width:{w}px">' for w in widths)+'</colgroup><thead><tr>'+''.join('<th>'+h+'</th>' for h in headings)+'</tr></thead><tbody>'+''.join(body)+'</tbody></table></div>'
    near=[r for r in rows if r['pool']=='expected' and (r['sort_date']<=(date.fromisoformat(as_of)+timedelta(days=105)).isoformat() or r['color'] in ('green','teal'))]
    applied=[r for r in rows if r['pool']=='applied'];approved=[r for r in rows if r['pool']=='approved']
    rule_links=' · '.join(source_link({'url':url,'title':board}) for board,url in SOURCES.items())
    doc_count=sum(len(r['documents']) for r in rows);complete=sum(d.get('body_complete') is True for r in rows for d in r['documents'])
    checked=freshness.get('checked_at','未进行独立实时刷新')
    refreshed=freshness.get('companies',[])
    freshness_text=f'近期重新核查{len(refreshed)}家公司，请求失败{sum(bool(r.get("errors")) for r in refreshed)}家；渲染本身不联网。'
    coverage=(f'当期东方财富ST板块：{current["ordinary_count"]}家普通ST；报告保留{len(rows)}家，当前名单遗漏{len(missing)}家。' if current else '未取得完整当期ST名单，不能声称全量。')
    css='''body{font:14px/1.7 "Microsoft YaHei",system-ui;margin:0;padding:32px;background:#f5f7fb;color:#26354a}h1{font-size:27px;margin:0 0 8px}h2{font-size:20px;margin:28px 0 12px}h3{font-size:16px}p{margin:8px 0}.intro{max-width:1250px}.muted,.sub{color:#78869a;font-size:12px}.sub{display:block;margin-top:8px}.note{background:white;border:1px solid #dfe5ed;border-left:4px solid #6c83a6;border-radius:7px;padding:18px 22px;margin:18px 0}.stats{display:flex;gap:16px;flex-wrap:wrap}.stats span{background:white;border:1px solid #e1e6ef;border-radius:8px;padding:12px 18px}.stats b{font-size:22px;color:#264669;margin-right:6px}.legend{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:10px;margin:18px 0}.badge{padding:10px 12px;border-radius:6px;font-size:12px;display:flex;align-items:center;min-height:40px;box-sizing:border-box}.status{display:inline-flex;gap:6px;padding:6px 9px;border-radius:5px;font-size:12px;font-weight:600;line-height:1.6}.status:before,.badge:before{content:"";height:7px;width:7px;background:currentColor;border-radius:50%;flex-shrink:0;align-self:center;margin-right:6px}.green{background:#e8f5ee;color:#257447}.teal{background:#e5f4f4;color:#187576}.amber{background:#fff4db;color:#94620c}.blue{background:#eaf0fd;color:#3e63a5}.red{background:#fcecec;color:#a34c4c}.purple{background:#f1eafb;color:#7953a4}.slate{background:#e8edf2;color:#4c6078}.table-wrap{overflow:auto;max-height:800px;border:1px solid #dce3ee;border-radius:8px;background:white}table{border-collapse:separate;border-spacing:0;table-layout:fixed;width:2265px;background:white}th,td{padding:14px 16px;vertical-align:top;text-align:left;border-bottom:1px solid #e5ebf3;overflow-wrap:anywhere}th{position:sticky;top:0;background:#edf2f8;color:#55657d;font-size:12px;z-index:3}tbody tr:hover td{background:#fafcff}th:first-child,td:first-child{position:sticky;left:0;background:#fff;z-index:1;text-align:center}th:nth-child(2),td:nth-child(2){position:sticky;left:50px;background:#fff;z-index:1;border-right:1px solid #e3e9f1}th:first-child,th:nth-child(2){background:#edf2f8;z-index:4}.rank{color:#8896a8;font-variant-numeric:tabular-nums}.identity strong{font-size:16px;color:#284569}.onset{margin-top:14px;padding-top:10px;border-top:1px solid #e5ebf3;font-size:12px}.onset b{color:#77869c}.date{font-variant-numeric:tabular-nums}.risk{border-left:3px solid #ccd7e5;padding:8px 10px;background:#f5f7fa;font-size:12px}.risk.loss{border-color:#d89682;background:#fff1ec;color:#964e3d}.catalyst{color:#395a81;font-size:13px}a{color:#3264a9;text-decoration:none}a:hover{text-decoration:underline}input,select{font:inherit;background:white;border:1px solid #d7e0eb;border-radius:6px;padding:10px 12px;margin:0 8px 12px 0}input{width:240px}summary{color:#3264a9;cursor:pointer}details{font-size:12px;margin-top:12px}ul{padding-left:16px}nav{display:flex;gap:16px;margin:18px 0;flex-wrap:wrap}@media(max-width:900px){body{padding:20px 14px}.legend{grid-template-columns:repeat(2,minmax(0,1fr))}}'''
    page=f'''<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>普通ST未来摘帽研究 · {as_of}</title><style>{css}</style></head><body>
<h1>普通ST未来摘帽研究 · {as_of}</h1><p class="muted">最新状态核查：{esc(checked)} · 规则核对：2026-10-18 · 版本：按原因匹配撤销路径</p>
<div class="stats"><span><b>{len(rows)}</b>研究公司</span><span><b>{len(applied)}</b>已申请待审核</span><span><b>{len(approved)}</b>已披露撤销决定</span><span><b>{sum(r['loss_filter']=='loss' for r in rows)}</b>最新一期存在亏损</span></div>
<div class="note intro"><b>先看为何ST，再看能否撤销，最后单独判断投资风险。</b><p>{esc(coverage)}排除*ST；沪深口径，北交所未独立验证。共{doc_count}份材料，{complete}份正文完整，其他保留缺口；{esc(freshness_text)}</p><p>“连续亏损＋持续经营不确定性”类，规则允许最近年度经审计扣非前后孰低利润为正，<b>或者</b>持续经营不确定性消除；不是统一等待三年盈利。处罚类则看正式决定满12个月、更正及必要材料，不另设所有公司都必须盈利的门槛。半年盈利不能替代年审。</p><p>颜色表示事件证据进度，亏损另用橙红风险框；绿色不代表整体获准或适合买入。日期排序仅为条件窗口，精确收件周年仍不是已核准的最早申请日。实际公开时间与正式公告日分别保存，避免漏掉当天已公开、落款下一交易日的决定。</p><p>现行规则：{rule_links} · <a href="规则与研究方法说明.md">规则与方法说明</a></p></div>
<nav><a href="#near">近期申请前预期</a><a href="#applied">已申请依据</a><a href="#approved">已获准/生效案例</a><a href="#all">全量表与筛选</a></nav>
<div class="legend"><span class="badge green">整改证据较充分，主要等期限</span><span class="badge teal">实质事项已披露解决，待核查</span><span class="badge amber">期限可观察，关键材料仍待核</span><span class="badge blue">等待审计或经营验证</span><span class="badge red">仍有实质障碍或证据缺口</span><span class="badge purple">已申请，尚待审核</span><span class="badge slate">已披露撤销决定，核对生效日</span><span class="badge red">亏损风险独立列示，不替代规则</span></div>
<h2 id="near">近期申请前预期 · 条件窗口由近到远</h2><p class="muted">横向滚动可看完整规则与分析；公司、本轮ST时间固定在左侧。未定日期排后，研究次序不是收益排名。</p>{company_table(near,'near-table')}
<h2 id="applied">已实际申请 · 原因、申请依据及审核障碍</h2>{company_table(applied,'applied-table')}
<h2 id="approved">已披露撤销决定 · 区分待生效与已生效</h2>{company_table(approved,'approved-table')}
<h2 id="all">{len(rows)}家公司全量对照 · 原因、时间与撤销路径直接展示</h2>
<select id="pool" onchange="filterRows()"><option value="">全部状态</option><option value="expected">申请前预期</option><option value="applied">已申请待审核</option><option value="approved">已披露撤销决定</option></select>
<select id="order" onchange="sortRows()"><option value="date">条件窗口：近 → 远</option><option value="priority">事件证据成熟度优先</option></select>
<select id="loss" onchange="filterRows()"><option value="">盈亏：全部</option><option value="profit">最新一期归母、扣非均不亏损（投资筛选）</option><option value="loss">最新一期存在亏损</option><option value="unknown">数据缺失待核</option></select>
<input id="q" placeholder="搜索公司、原因、条款或月份" oninput="filterRows()">
<select id="stage" onchange="filterRows()"><option value="">全部研究判断</option>{''.join('<option>'+esc(k)+'</option>' for k in result['categories'])}</select>
{company_table(rows,'full')}
<script>function filterRows(){{let q=document.getElementById('q').value.toLowerCase(),s=document.getElementById('stage').value,p=document.getElementById('pool').value,l=document.getElementById('loss').value;document.querySelectorAll('#full tbody tr').forEach(r=>r.hidden=!(r.textContent.toLowerCase().includes(q)&&(!s||r.dataset.level===s)&&(!p||r.dataset.pool===p)&&(!l||r.dataset.loss===l)));}}function sortRows(){{let b=document.querySelector('#full tbody'),p=document.getElementById('order').value,o={{expected:0,applied:1,approved:2}};Array.from(b.rows).sort((a,c)=>{{let pool=o[a.dataset.pool]-o[c.dataset.pool];if(pool)return pool;let d=a.dataset.date.localeCompare(c.dataset.date),r=Number(a.dataset.priority)-Number(c.dataset.priority);return p==='priority'?(r||d):(d||r);}}).forEach(r=>b.appendChild(r));}}</script></body></html>'''
    (root/'ST摘帽预期研究报告.html').write_text(page,encoding='utf-8')
    lines=[f'# 普通ST未来摘帽研究（{as_of}）','',f'最新状态核查：{checked}。{coverage}','',
           '颜色为事件进度，盈亏为独立经营风险；规则来源及方法见[说明](规则与研究方法说明.md)。满期、申请、获准和生效分别处理。','',
           '|公司|本轮ST时间|实际原因|状态|规则及撤销条件|为何可能摘帽/申请依据|尚缺条件|时间|财务及风险|',
           '|---|---|---|---|---|---|---|---|---|']
    for r in rows:
        vals=[r['name']+' '+r['code'],r['onset_display'],r.get('trigger_detail') or '；'.join(r['reasons']),r['readiness'],r['rule_route'],r['removal_thesis'],r['remaining_barriers'],r['window'],r['latest_profit_status']+'；'+r['annual_profit_status']+'；'+r['profit_warning']]
        lines.append('|'+ '|'.join(str(v).replace('|','/').replace('\n',' ') for v in vals)+'|')
    (root/'ST摘帽预期研究报告.md').write_text('\n'.join(lines),encoding='utf-8')
    print(json.dumps({k:result[k] for k in ('as_of','company_count','missing_current','categories','pools')},ensure_ascii=False))
    return result


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('root',type=Path)
    p.add_argument('--as-of',required=True)
    p.add_argument('--overrides')
    a=p.parse_args();render(a.root,a.as_of,a.overrides)
