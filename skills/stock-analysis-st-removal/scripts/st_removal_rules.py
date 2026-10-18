"""Match reviewed active ST causes to board-specific removal routes.

Routes explain conditions; they never certify that a company satisfies them.
"""
import re

SOURCES = {
    '沪市主板': 'https://www.sse.com.cn/lawandrules/sselawsrules2025/stocks/mainipo/c/c_20260424_10816589.shtml',
    '深市主板': 'https://docs.static.szse.cn/www/lawrules/rule/stock/W020260424747613955674.pdf',
    '创业板': 'https://docs.static.szse.cn/www/lawrules/rule/stock/W020260424688875101057.pdf',
    '科创板': 'https://www.sse.com.cn/lawandrules/sselawsrules2025/stocks/staripo/c/c_20260424_10816592.shtml',
}


def disclosed_stage(packet, as_of, onset_display=""):
    """Promote only a full removal notice; partial removal is not approval."""
    docs = sorted(packet.get('documents', []), key=lambda d:d['date'], reverse=True)
    reset = max((d['date'] for d in docs if re.search(r'叠加.*风险警示|实施.*风险警示.*停',d['title'])), default='')
    onset = re.search(r'20\d{2}-\d{2}-\d{2}', onset_display)
    if onset:
        reset = max(reset,onset[0])
    for d in docs:
        if d['date'] > as_of or d['date'] < reset:
            continue
        title = d['title']
        if ('撤销其他风险警示' in title and
                not any(x in title for x in ('申请','部分','继续','风险提示')) and d.get('body_complete')):
            text = re.sub(r'\s+', '', d.get('body', ''))
            m = re.search(r'撤销(?:其他风险警示)?(?:的)?(?:起始日|生效日)[：:为]*(20\d{2})年(\d{1,2})月(\d{1,2})日',text)
            effective = f'{int(m[1]):04d}-{int(m[2]):02d}-{int(m[3]):02d}' if m else None
            active = bool(effective and effective <= as_of)
            return dict(level='已生效撤销' if active else '已获准待生效',
                        effective_date=effective, status_evidence={k:d.get(k) for k in ('date','notice_date','published_at','title','url')},
                        latest_status='已经生效' if active else '已披露撤销决定，等待生效')
    return {}


def attach_rules(row):
    code = row['code']
    board = ('科创板' if code.startswith('688') else '创业板' if code.startswith(('300','301'))
             else '沪市主板' if code.startswith('6') else '深市主板')
    common = {'沪市主板':'9.8.7','深市主板':'9.8.7','创业板':'9.10','科创板':'12.9.7'}[board]
    penalty = {'沪市主板':'9.8.8','深市主板':'9.8.8','创业板':'9.11','科创板':'12.9.8'}[board]
    funds = {'沪市主板':'9.8.6','深市主板':'9.8.7','创业板':'9.10','科创板':'12.9.6'}[board]
    subtype = row.get('operating_subtype', '待核具体经营/持续经营触发项')
    routes = []
    for reason in row.get('reasons', []):
        clause = common
        if reason == '处罚/虚假记载':
            clause = penalty
            condition = '正式决定作出满12个月＋处罚涉及年度追溯重述；核查专项材料及已提诉讼预计负债'
            relation = '此处罚项无独立当年盈利要求；亏损仍需排查叠加持续经营或财务退市风险'
        elif reason == '内控非标':
            condition = ('缺陷消除并有效运行，最近年度标准无保留内控审计' if board in ('沪市主板','科创板')
                         else '缺陷消除并有效运行，最近年度无保留意见内控审计')
            relation = '盈利不能替代内控有效；亏损不是本项单独否决条件'
        elif reason == '资金占用':
            clause = funds
            condition = '占用实质清偿/消除＋会计师专项核查'
            relation = '利润不能代替清偿到账与核查'
        elif reason == '违规担保':
            clause = funds
            condition = '违规担保责任消除＋律师法律意见等必要材料'
            relation = '利润不能代替责任消除'
        elif reason == '账户冻结':
            condition = '导致警示的主要账户冻结等情形消除，核对解冻范围及经营恢复'
            relation = '盈利不能代替解冻；部分账户不自动等于主要账户触发项'
        elif reason == '经营/持续经营' and subtype == '连续亏损并持续经营不确定性':
            condition = '最近年度经审计扣非前后净利润孰低者为正，或者持续经营不确定性消除；提交年审材料'
            relation = '二选一路径，非必须连续三年盈利；半年扭亏不能替代年审'
        elif reason == '经营/持续经营' and subtype == '生产经营严重受影响':
            condition = '生产销售实际恢复、导致警示的严重经营影响消除'
            relation = '不套连续亏损条款；不机械等年报，但恢复证据须充分'
        elif reason == '经营/持续经营':
            condition = '先核实际触发条款：持续经营、停产、其他前景不明等应分别匹配，不能统一要求盈利'
            relation = '复合/兜底情形须专项核对，盈利本身不证明全部原因消除'
        elif reason == '分红不达标':
            condition = '按板块核对年度审计利润及股东会分配方案，相关不足情形消除'
            relation = '具有特定利润及分红要求，不适用处罚周年路径'
        else:
            condition = '具体有效触发条款及对应纠正/补披露/整改条件待核'
            relation = '不能只按未披露或亏损字样推算'
        routes.append(dict(reason=reason,clause=clause,condition=condition,profit_relation=relation))
    row['board'] = board
    row['rule_source'] = SOURCES[board]
    row['rule_analysis'] = routes
    row['rule_route'] = '；'.join(f"{r['reason']}〔{r['clause']}〕：{r['condition']}" for r in routes)
    row['profit_relation'] = '；'.join(dict.fromkeys(r['profit_relation'] for r in routes))
    row['removal_thesis'] = row.get('removal_thesis') or row['reasoning']
    row['remaining_barriers'] = row.get('remaining_barriers') or '；'.join(row.get('pending', []))
    return row
