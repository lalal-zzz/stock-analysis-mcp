"""
cli.py — 重建 / 回填 / 采集 / 清理 / 形态分析 统一命令行入口
(移植自"股票信息"项目 rebuild_stock_db.py / backfill_daily_spot.py /
旧版行情采集脚本的 CLI 部分)

用法:
    python -m stock_analysis_mcp.cli rebuild --dry-run          # 预览步骤, 不联网
    python -m stock_analysis_mcp.cli rebuild --workers 8 --with-sectors
    python -m stock_analysis_mcp.cli backfill --start 2026-01-01
    python -m stock_analysis_mcp.cli daily-capture              # 晚间采集(任务计划用)
    python -m stock_analysis_mcp.cli cleanup --dry-run
    python -m stock_analysis_mcp.cli pattern-scan --universe sectors --date 2026-08-14
    python -m stock_analysis_mcp.cli wave-analysis --symbol 600000
    python -m stock_analysis_mcp.cli pattern-backtest --sample 300 --workers 8
    python -m stock_analysis_mcp.cli pattern-optimize --cache signals.csv --universe stocks
"""

import argparse
import json
import os
import sys


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="stock-analysis",
        description="股票分析 MCP + Skills: 本地库重建/回填/采集/清理 + 形态扫描与回测")
    sub = p.add_subparsers(dest="command", required=True)

    def add_common(sp: argparse.ArgumentParser) -> None:
        sp.add_argument("--data-dir", default=None,
                        help="覆盖数据根目录 (等价 STOCK_ANALYSIS_DATA_DIR)")
        sp.add_argument("--dry-run", action="store_true",
                        help="只打印执行计划, 不联网不写库")

    # ── rebuild ──
    r = sub.add_parser("rebuild", help="步骤化全量重建本地库 (断点续传)")
    add_common(r)
    r.add_argument("--force", action="store_true", help="重建前删除旧数据库")
    r.add_argument("--workers", type=int, default=8)
    r.add_argument("--skip-spot", action="store_true")
    r.add_argument("--skip-kline", action="store_true")
    r.add_argument("--skip-rank", action="store_true")
    r.add_argument("--skip-combined", action="store_true")
    r.add_argument("--skip-indicators", action="store_true")
    r.add_argument("--with-sectors", action="store_true", help="含板块全量K线+指标缓存")
    r.add_argument("--sector-limit", type=int, default=5000)

    # ── backfill ──
    b = sub.add_parser("backfill", help="缺口检测补齐 (history/rank/combined)")
    add_common(b)
    b.add_argument("--start", default=None)
    b.add_argument("--end", default=None)
    b.add_argument("--no-history", action="store_true")
    b.add_argument("--no-rank", action="store_true")
    b.add_argument("--no-combined", action="store_true")
    b.add_argument("--symbols", default=None, help="逗号分隔股票代码 (默认全部)")
    b.add_argument("--workers", type=int, default=8)

    # ── daily-capture ──
    d = sub.add_parser("daily-capture", help="晚间采集: xuangu排名+spot快照+K线增量+指标缓存")
    add_common(d)
    d.add_argument("--no-spot", action="store_true")
    d.add_argument("--no-kline", action="store_true")
    d.add_argument("--no-indicators", action="store_true")
    d.add_argument("--page-size", type=int, default=500)
    d.add_argument("--max-pages", type=int, default=None)
    d.add_argument("--workers", type=int, default=8)

    # ── cleanup ──
    c = sub.add_parser("cleanup", help="清理冗余/过期数据 + VACUUM")
    add_common(c)
    c.add_argument("--no-vacuum", action="store_true")

    # ── pattern-scan ──
    s = sub.add_parser("pattern-scan", help="形态扫描 (股票/板块, 依赖本地库K线)")
    s.add_argument("--universe", default="stocks", choices=("stocks", "sectors"))
    s.add_argument("--date", default=None, help="扫描日期, 默认本地库最新交易日")
    s.add_argument("--patterns", default=None, help="逗号分隔形态 key/中文名 (默认全部)")
    s.add_argument("--strict", action="store_true", help="只留优中选优档信号")
    s.add_argument("--no-filter", action="store_true", help="不过滤返回全部原始信号")
    s.add_argument("--workers", type=int, default=8)
    s.add_argument("--symbols", default=None, help="逗号分隔标的代码/名称 (默认全部)")
    s.add_argument("--sector-type", choices=("concept", "industry"),
                   help="板块类型 (仅 sectors 宇宙)")
    s.add_argument("--json", action="store_true", help="以 JSON 输出信号")

    # ── wave-analysis ──
    w = sub.add_parser("wave-analysis", help="波浪/Fibonacci 结构分析 (单标的)")
    w.add_argument("--universe", default="stocks", choices=("stocks", "sectors"))
    w.add_argument("--symbol", required=True, help="股票/板块代码或名称")
    w.add_argument("--start", default=None, help="起始日期, 默认 2010-01-01")
    w.add_argument("--end", default=None, help="截止日期, 默认本地库最新")
    w.add_argument("--tail", type=int, default=720, help="只分析最近 N 根K线")
    w.add_argument("--pivot-left", type=int, default=5)
    w.add_argument("--pivot-right", type=int, default=5)
    w.add_argument("--swing-min", type=float, default=0.03, help="zigzag 最小摆动幅度")
    w.add_argument("--json", action="store_true", help="以 JSON 输出完整结果")

    # ── pattern-backtest / pattern-optimize (转发, 剩余参数原样透传) ──
    sub.add_parser("pattern-backtest", help="形态历史回测 (详见 strategies/pattern_backtest.py)")
    sub.add_parser("pattern-optimize", help="形态参数优化 (详见 strategies/pattern_optimize.py)")

    for command in ("data-audit", "data-repair"):
        maintenance = sub.add_parser(command, help="数据完整性审计 / 可恢复的全历史修复")
        add_common(maintenance)
        maintenance.add_argument("--symbols", default=None)
        maintenance.add_argument("--adjust", choices=("all", "qfq", "hfq", "raw"), default="all")
        maintenance.add_argument("--max-jobs", type=int, default=None)
        maintenance.add_argument("--output", default=None)
        maintenance.add_argument("--with-sectors", action="store_true")
    alerts = sub.add_parser("alerts-watch", help="持续评估配置的价格区域，事件写入本地库")
    alerts.add_argument("--rules", required=True, help="ZoneRule JSON数组；价格为不复权口径")
    alerts.add_argument("--poll-seconds", type=float, default=60)
    alerts.add_argument("--max-polls", type=int, default=0)
    alerts.add_argument("--data-dir", default=None)
    restore = sub.add_parser("data-restore", help="恢复指定修复归档批次，同时归档当前价格")
    restore.add_argument("--batch-id", required=True)
    restore.add_argument("--data-dir", default=None)
    rules = sub.add_parser("strategy-rule", help="候选规则提案、人工批准启用与回滚")
    rules.add_argument("action", choices=("propose", "activate", "rollback", "status"))
    rules.add_argument("--registry", required=True)
    rules.add_argument("--filters", default=None)
    rules.add_argument("--evidence", default=None)
    rules.add_argument("--version", default=None)
    rules.add_argument("--approved-by", default=None)
    rules.add_argument("--effective-date", default=None)
    return p


def _parse_patterns(text: str | None) -> list[str] | None:
    if not text:
        return None
    from .strategies.patterns import PATTERN_NAMES
    keys = []
    for tok in text.replace("，", ",").split(","):
        tok = tok.strip()
        if tok in PATTERN_NAMES:
            keys.append(tok)
        else:
            matched = [k for k, cn in PATTERN_NAMES.items() if tok and (tok in cn or cn in tok)]
            keys.extend(matched)
    return list(dict.fromkeys(keys)) or None


def _cmd_rebuild(a) -> int:
    from .data.build import rebuild_full_data
    return rebuild_full_data(
        force=a.force, workers=a.workers,
        skip_spot=a.skip_spot, skip_kline=a.skip_kline, skip_rank=a.skip_rank,
        skip_combined=a.skip_combined, skip_indicators=a.skip_indicators,
        with_sectors=a.with_sectors, sector_limit=a.sector_limit,
        dry_run=a.dry_run)


def _cmd_backfill(a) -> int:
    from .data.build import backfill_data
    symbols = a.symbols.split(",") if a.symbols else None
    return backfill_data(
        start=a.start, end=a.end,
        do_history=not a.no_history, do_rank=not a.no_rank,
        do_combined=not a.no_combined, symbols=symbols,
        max_workers=a.workers, dry_run=a.dry_run)


def _cmd_daily_capture(a) -> int:
    from .data.build import daily_capture
    daily_capture(
        save_spot=not a.no_spot, save_kline=not a.no_kline,
        save_indicators=not a.no_indicators,
        page_size=a.page_size, max_pages=a.max_pages,
        workers=a.workers, dry_run=a.dry_run)
    # daily_capture 返回采集行数, 不能作为进程退出码(任务计划会误判失败)
    return 0


def _cmd_cleanup(a) -> int:
    from .data.build import cleanup_database
    return cleanup_database(dry_run=a.dry_run, vacuum=not a.no_vacuum)


def _cmd_pattern_scan(a) -> int:
    from .strategies.patterns import scan_universe
    symbols = a.symbols.split(",") if a.symbols else None
    sigs = scan_universe(
        a.universe, date=a.date, patterns=_parse_patterns(a.patterns),
        strict=a.strict, no_filter=a.no_filter,
        workers=a.workers, symbols=symbols, sector_type=a.sector_type)
    if a.json:
        print(json.dumps(sigs, ensure_ascii=False, indent=2))
    elif sigs:
        print(f"共 {len(sigs)} 个信号 ({a.universe}):")
        for s in sigs:
            print(f"  {s['date']} {s['symbol']} {s.get('name', '')} "
                  f"[{s['pattern_cn']}/{s['variant']}] score={s['score']} "
                  f"resonance={s['resonance']} levels={s['hit_levels']} "
                  f"{'(strict)' if s.get('strict_pass') else ''}")
    else:
        print("无信号")
    return 0


def _cmd_wave_analysis(a) -> int:
    from .strategies.wave_analysis import analyze_wave
    rep = analyze_wave(
        a.universe, a.symbol, start=a.start or "2010-01-01", end=a.end, tail=a.tail,
        pivot_left=a.pivot_left, pivot_right=a.pivot_right, swing_min=a.swing_min)
    if a.json:
        print(json.dumps(rep, ensure_ascii=False, indent=2))
        return 0

    print(f"{rep['date']} {rep['symbol']} {rep.get('name', '')} "
          f"close={rep['close']} pivots={rep['pivot_count']}")
    impulse = rep["elliott_like_impulse"]
    correction = rep["abc_like_correction"]
    print(f"5浪匹配: {impulse['structure']} direction={impulse.get('direction', '')} "
          f"score={impulse['score']}")
    for c in impulse.get("checks", [])[:6]:
        print(f"  {c}")
    print(f"ABC修正匹配: {correction['structure']} direction={correction.get('direction', '')} "
          f"score={correction['score']}")
    for c in correction.get("checks", [])[:4]:
        print(f"  {c}")

    print("Fibonacci 转折统计:")
    labels = {"up_pullback": "上涨后回调", "down_rebound": "下跌后反弹"}
    for key, title in labels.items():
        item = rep["fib_summary"].get(key, {})
        print(f"  {title}: 样本={item.get('total', 0)} "
              f"均值={item.get('avg_ratio')} 常见={item.get('most_common', '')} "
              f"分布={item.get('distribution', {})}")

    if rep["fib_turns"]:
        print("最近转折:")
        for t in rep["fib_turns"][-6:]:
            print(f"  {t['turn_date']} {labels.get(t['kind'], t['kind'])} "
                  f"ratio={t['ratio']} near={t['nearest_fib']} price={t['turn_price']}")

    active = rep.get("active_leg") or {}
    if active:
        print(f"当前段: {active['direction']} from {active['from_date']} "
              f"{active['from_price']} -> {active['current_close']} "
              f"({active['change_pct']}%), {active['status']}")
        print(f"结束判定: {active['end_rule']}")
        levels = active.get("reference_levels") or {}
        if levels:
            print(f"参考位: {levels}")
    print("提示: Fibonacci/波浪只作结构证据, 需要和趋势、量能、失效位一起看。")
    return 0


def main(argv: list[str] | None = None) -> int:
    # parse_known_args: pattern-backtest/optimize 的选项原样透传给子模块
    # (REMAINDER 对紧跟子命令的 --xxx 会被主解析器误吃, bpo-2962)
    args, remaining = _build_parser().parse_known_args(argv)
    if getattr(args, "data_dir", None):          # 数据目录覆盖 (等价 STOCK_ANALYSIS_DATA_DIR)
        os.environ["STOCK_ANALYSIS_DATA_DIR"] = args.data_dir
    if args.command in {"data-audit", "data-repair"}:
        from .data.repair import audit_data, repair_data
        if remaining:
            return 2
        symbols = args.symbols.split(",") if args.symbols else None
        adjustments = ("qfq", "hfq", "") if args.adjust == "all" else ("" if args.adjust == "raw" else args.adjust,)
        report = (audit_data(symbols) if args.command == "data-audit" else
                  repair_data(symbols=symbols, adjustments=adjustments, dry_run=args.dry_run,
                              max_jobs=args.max_jobs, output=args.output, with_sectors=args.with_sectors))
        if args.output and (args.command == "data-audit" or args.dry_run):
            from pathlib import Path
            path = Path(args.output)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        summary = {k:v for k,v in report.items() if k not in {"jobs", "results"}} if args.output else report
        print(json.dumps(summary, ensure_ascii=False, indent=2))
        return 1 if report.get("status") == "partial" else 0
    if args.command == "alerts-watch":
        if remaining:
            return 2
        from .alerts.service import watch_zones
        return watch_zones(args.rules, poll_seconds=args.poll_seconds, max_polls=args.max_polls)
    if args.command == "data-restore":
        if remaining:
            return 2
        from .data.restore import restore_history
        print(json.dumps(restore_history(args.batch_id), ensure_ascii=False, indent=2))
        return 0
    if args.command == "strategy-rule":
        from .strategies.rule_registry import propose, activate, read_registry
        from pathlib import Path
        if remaining:
            return 2
        if args.action == "propose":
            filters = json.loads(Path(args.filters).read_text(encoding="utf-8"))
            print(propose(args.registry, filters, evidence=args.evidence))
        elif args.action in {"activate", "rollback"}:
            if not args.version or not args.approved_by or not args.effective_date:
                raise ValueError("version, approved-by and effective-date required")
            activate(args.registry, args.version, approved_by=args.approved_by,
                     effective_date=args.effective_date, rollback=args.action == "rollback")
        else:
            print(json.dumps(read_registry(args.registry), ensure_ascii=False, indent=2))
        return 0

    handlers = {
        "rebuild": _cmd_rebuild,
        "backfill": _cmd_backfill,
        "daily-capture": _cmd_daily_capture,
        "cleanup": _cmd_cleanup,
        "pattern-scan": _cmd_pattern_scan,
        "wave-analysis": _cmd_wave_analysis,
    }
    if args.command in handlers:
        if remaining:
            print(f"未知参数: {' '.join(remaining)}", file=sys.stderr)
            return 2
        return handlers[args.command](args)

    if args.command == "pattern-backtest":
        from .strategies import pattern_backtest
        try:
            pattern_backtest.main(remaining)
            return 0
        except SystemExit as e:            # argparse --help / 参数错误
            return int(e.code or 0)
        except Exception:
            return 1
    if args.command == "pattern-optimize":
        from .strategies import pattern_optimize
        try:
            pattern_optimize.main(remaining)
            return 0
        except SystemExit as e:
            return int(e.code or 0)
        except Exception:
            return 1
    _build_parser().print_help()
    return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
