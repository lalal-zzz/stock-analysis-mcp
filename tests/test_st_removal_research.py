import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "skills" / "stock-analysis-st-removal" / "scripts"))
import unittest
from st_research_report import analyse, presentation, render
from st_research_collect import document,current_universe,notices,publication_day
from st_removal_rules import attach_rules,disclosed_stage


def packet(body,title='关于实施其他风险警示暨停牌的公告'):
    return dict(code='600001',name='ST测试',documents=[dict(id='A',date='2026-04-30',title=title,url='https://example.com/a',body=body)],financials=[],announcement_titles=[],errors=[])


class ResearchTests(unittest.TestCase):
    def test_receipt_does_not_use_later_publication_date(self):
        p=packet('公司于2025年12月29日收到行政处罚决定书。2025年12月30日在网站披露《关于收到行政处罚决定书的公告》。')
        a=analyse(p,'2026-10-18')
        self.assertEqual(a['penalty_anchor']['date'],'2025-12-29')
        self.assertEqual(a['penalty_anchor']['basis'],'收到')

    def test_quoted_waiting_period_cannot_capture_correction_date(self):
        p=packet('自中国证监会作出行政处罚决定书之日起已满十二个月。整改完毕：2024年3月15日披露更正公告。')
        self.assertIsNone(analyse(p,'2026-10-18')['penalty_anchor'])

    def test_standard_disclaimer_is_not_fraud_trigger(self):
        p=packet('本公司保证不存在虚假记载。公司2025年度内部控制审计被出具否定意见，因此实施其他风险警示。')
        self.assertNotIn('处罚/虚假记载',analyse(p,'2026-10-18')['reasons'])

    def test_partial_removal_not_full_approval(self):
        p=packet('仅撤销部分，仍保留其他事项。','关于撤销部分其他风险警示暨继续实施其他风险警示的公告')
        p['announcement_titles']=[dict(title=p['documents'][0]['title'],notice_date='2026-04-30')]
        self.assertNotEqual(analyse(p,'2026-10-18')['level'],'已退出普通ST/状态变化')

    def test_onset_is_not_stop_trading_date(self):
        p=packet('停牌日期为2026年4月28日。实施起始日为2026年4月29日。')
        self.assertEqual(analyse(p,'2026-10-18')['onset'][0]['date'],'2026-04-29')

    def test_content_pagination_join_and_limit(self):
        class Fake:
            def get(self,url,params):
                return dict(success=1,data=dict(art_code='A',page_size=2,notice_content='原始原因' if params['page_index']==1 else '尚未全部清偿'))
        row=dict(art_code='A',title='进展公告',notice_date='2026-10-01')
        full=document(Fake(),'600001',row,2)
        self.assertIn('尚未全部清偿',full['body'])
        self.assertTrue(full['body_complete'])
        self.assertFalse(document(Fake(),'600001',row,1)['body_complete'])

    def test_current_universe_repeated_page_is_failure(self):
        class Fake:
            def get(self,*args):return dict(rc=0,data=dict(total=101,diff={'0':dict(f12='600001',f14='ST测试',f13=1)}))
        with self.assertRaises(ValueError):current_universe(Fake())




class PresentationTests(unittest.TestCase):
    def row(self, **kw):
        r=dict(level='处罚满期观察', window='日期待核', pending=['专项核查'], documents=[])
        r.update(kw)
        return r

    def test_elapsed_anniversary_not_future_candidate(self):
        r=presentation(self.row(window_start='2025-10-01'), '2026-10-18')
        self.assertIsNone(r['window_start'])
        self.assertIn('原期限已过',r['date_basis'])

    def test_near_date_does_not_claim_conditions_met(self):
        r=presentation(self.row(window_start='2026-10-24'), '2026-10-18')
        self.assertEqual(r['color'],'amber')
        self.assertIn('待核',r['readiness'])

    def test_applied_is_separate_and_does_not_predict_approval_date(self):
        r=presentation(self.row(level='已申请（非提前预期池）',window_start='2026-10-24'), '2026-10-18')
        self.assertEqual(r['pool'],'applied')
        self.assertIsNone(r['window_start'])


class LossGateTests(unittest.TestCase):
    def test_loss_risk_is_independent_from_penalty_evidence(self):
        r=presentation(dict(level='未来90日条件型候选',readiness='整改证据较充分，主要等期限',window='待核',pending=[],documents=[],latest_financial=dict(REPORT_DATE='2026-06-30',PARENTNETPROFIT=-100,KCFJCXSYJLR=-200)), '2026-10-18')
        self.assertEqual(r['color'],'green')
        self.assertEqual(r['loss_filter'],'loss')
        self.assertIn('经营风险',r['profit_warning'])
        self.assertIn('整改证据较充分',r['readiness'])

    def test_positive_net_but_negative_core_fails_profit_gate(self):
        r=presentation(dict(level='处罚满期观察',window='待核',pending=[],documents=[],latest_financial=dict(REPORT_DATE='2026-06-30',PARENTNETPROFIT=100,KCFJCXSYJLR=-200)), '2026-10-18')
        self.assertEqual(r['loss_filter'],'loss')


class CauseAndFreshnessTests(unittest.TestCase):
    def test_publication_today_includes_tomorrow_printed_notice(self):
        class Fake:
            def get(self, *a, **kw):
                return dict(success=1,data=dict(total_hits=1,list=[dict(art_code='A',notice_date='2026-10-19',display_time='2026-10-18 15:30:41:000',title='撤销其他风险警示')]))
        rows,coverage=notices(Fake(),'600001','2026-10-18','2026-10-18',2)
        self.assertEqual(len(rows),1)
        self.assertTrue(coverage['complete'])
        self.assertEqual(publication_day(rows[0]),'2026-10-18')

    def test_today_cannot_include_tomorrow_publication(self):
        class Fake:
            def get(self,*a,**kw):
                return dict(success=1,data=dict(total_hits=1,list=[dict(art_code='A',notice_date='2026-10-19',display_time='2026-10-19 10:00:00',title='撤销其他风险警示')]))
        self.assertEqual(notices(Fake(),'600001','2026-10-18','2026-10-18',2)[0],[])

    def test_loss_related_rule_keeps_alternative_audited_route(self):
        r=attach_rules(dict(code='300001',reasons=['经营/持续经营'],operating_subtype='连续亏损并持续经营不确定性',reasoning='待年审',pending=[]))
        self.assertEqual(r['board'],'创业板')
        self.assertIn('或者持续经营不确定性消除',r['rule_route'])
        self.assertIn('9.10',r['rule_route'])

    def test_production_interruption_not_mapped_to_profit_condition(self):
        r=attach_rules(dict(code='688001',reasons=['经营/持续经营'],operating_subtype='生产经营严重受影响',reasoning='待恢复',pending=[]))
        self.assertIn('生产销售实际恢复',r['rule_route'])
        self.assertNotIn('利润孰低者为正',r['rule_route'])

    def test_all_current_causes_have_separate_routes(self):
        r=attach_rules(dict(code='002001',reasons=['处罚/虚假记载','账户冻结','经营/持续经营'],operating_subtype='连续亏损并持续经营不确定性',reasoning='逐项核对',pending=[]))
        self.assertEqual(len(r['rule_analysis']),3)
        self.assertEqual(r['rule_analysis'][0]['clause'],'9.8.8')

    def approval(self):
        return dict(documents=[dict(date='2026-10-18',notice_date='2026-10-19',published_at='2026-10-18 15:30:00',title='关于撤销其他风险警示暨临时停牌的公告',body='撤销起始日为2026年10月20日。',body_complete=True,url='https://example.com/decision')])

    def test_approval_is_not_effective_before_announced_date(self):
        self.assertEqual(disclosed_stage(self.approval(),'2026-10-18','2025-09-23')['level'],'已获准待生效')
        self.assertEqual(disclosed_stage(self.approval(),'2026-10-20','2025-09-23')['level'],'已生效撤销')

    def test_historical_removal_cannot_erase_reapplied_st(self):
        self.assertEqual(disclosed_stage(self.approval(),'2026-10-21','2026-10-21'),{})

    def test_partial_removal_not_complete_approval(self):
        p=self.approval();p['documents'][0]['title']='关于撤销部分其他风险警示的公告'
        self.assertEqual(disclosed_stage(p,'2026-10-18','2025-09-23'),{})


class ReportIntegrationTests(unittest.TestCase):
    def test_latest_decision_overrides_review_and_keeps_visible_cause_date(self):
        import tempfile,json,contextlib,io
        from pathlib import Path
        p=packet('因2022年收入虚假记载被实施其他风险警示。')
        p['documents'].append(dict(id='B',date='2026-10-18',notice_date='2026-10-19',published_at='2026-10-18 15:30:00',title='关于撤销其他风险警示暨临时停牌的公告',body='撤销起始日为2026年10月20日。',body_complete=True,url='https://example.com/decision'))
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);(root/'companies').mkdir()
            (root/'companies/600001.json').write_text(json.dumps(p,ensure_ascii=False),encoding='utf-8')
            (root/'universe.json').write_text(json.dumps(dict(total=1)),encoding='utf-8')
            notes=root/'reviewed.json';notes.write_text(json.dumps({'600001':dict(level='已申请（非提前预期池）',onset_display='2025-09-23',reasons=['处罚/虚假记载'],reasoning='更正后按处罚路径申请')}),encoding='utf-8')
            with contextlib.redirect_stdout(io.StringIO()):result=render(root,'2026-10-18',notes)
            row=result['companies'][0]
            self.assertEqual(row['pool'],'approved')
            self.assertEqual(row['effective_date'],'2026-10-20')
            text=(root/'ST摘帽预期研究报告.html').read_text(encoding='utf-8')
            # Identity/cause fields must precede disclosure details, not disappear into them.
            at=text.index('data-code="600001"');end=text.index('<details>',at)
            visible=text[at:end]
            self.assertIn('2025-09-23',visible)
            self.assertIn('处罚/虚假记载',visible)
            self.assertIn('9.8.8',visible)


if __name__=='__main__':unittest.main()
