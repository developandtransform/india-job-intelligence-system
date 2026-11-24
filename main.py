"""India Job Intelligence System - command line.

  python main.py demo      offline demo with invented sample jobs (no accounts needed)
  python main.py check     validate .env and Google Sheets access (no jobs fetched)
  python main.py validate-targets   test every ATS board in config/targets.yaml
  python main.py run       the real daily run
  python main.py run --dry-run   fetch + evaluate, write nothing, send nothing
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from config.settings import PROJECT_ROOT, load_settings
from core.http import HttpClient
from core.logging_setup import setup_logging


def _connectors(settings, http):
    from connectors.apify_connector import ApifyConnector
    from connectors.ats_connector import AtsConnector
    from connectors.career_page_connector import CareerPageConnector
    from connectors.jobspy_connector import JobSpyConnector
    return [JobSpyConnector(settings, http), AtsConnector(settings, http),
            CareerPageConnector(settings, http), ApifyConnector(settings, http)]


def _print_outcome(out) -> None:
    st = out.stats
    print(f"\nRun date: {out.today}")
    for r in out.results:
        print(f"  source {r.name:<13} {r.status:<8} jobs={len(r.jobs):<4} errors={len(r.errors)}")
    if st:
        print(f"  new={st.new} updated={st.updated} closed={st.closed} dropped(out of scope)={st.dropped_out_of_scope}")
    print(f"  sheet write: {out.save_info or 'none'}")
    print(f"  e-mail: {out.email_status}")


def cmd_demo(args) -> int:
    from demo.sample_jobs import DemoConnector
    from integrations.store import MemoryStore
    from processing.pipeline import run_pipeline
    settings = load_settings(environ={})
    setup_logging("WARNING")
    store = MemoryStore()
    out = run_pipeline(settings, store, [DemoConnector(settings, HttpClient())], send_mail=False)
    folder = Path(args.out)
    files = store.export_csv(folder)
    (folder / "digest_preview.html").write_text(out.digest.html, encoding="utf-8")
    (folder / "digest_preview.txt").write_text(out.digest.text, encoding="utf-8")
    _print_outcome(out)
    print(f"\nDemo output written to {folder}/ ({len(files)} CSV files + digest_preview.html). Sample data is invented.")
    for j in sorted(out.jobs, key=lambda x: -x.match_score):
        print(f"  [{j.category:<32}] {j.title} @ {j.company}: {j.match_explanation[:90]}")
    return 0


def cmd_check(args) -> int:
    settings = load_settings()
    setup_logging(settings.log_level, settings.secret_values())
    problems = settings.validate(need_sheets=True, need_email=settings.email_enabled)
    for p in problems:
        print("PROBLEM:", p)
    if any("GOOGLE" in p for p in problems):
        return 1
    from integrations.google_sheets import GoogleSheetsStore
    try:
        store = GoogleSheetsStore(settings)
        store.connect()
        store.prepare()
    except Exception as exc:  # noqa: BLE001
        print("Google Sheets check FAILED:", exc)
        return 1
    print("Google Sheets OK:", store.url())
    print("Apify actors allowed to run:", settings.enabled_apify_actors() or "none (disabled by default)")
    return 0 if not problems else 1


def cmd_validate_targets(args) -> int:
    """Try every configured ATS board once and report how many postings it returns (needs internet)."""
    import time
    from connectors.ats_connector import AtsConnector
    from connectors.base import ConnectorResult
    settings = load_settings()
    setup_logging(settings.log_level, settings.secret_values())
    http = HttpClient(settings.http_timeout_seconds, 1, settings.request_delay_seconds)
    conn = AtsConnector(settings, http)
    bad = 0
    for ats, ident, name in conn.boards:
        res = ConnectorResult("ats")
        try:
            conn.fetch_board(ats, ident, name, res, time.monotonic() + 120)
            ok = not res.errors
        except Exception as exc:  # noqa: BLE001
            res.errors.append(f"{type(exc).__name__}: {str(exc)[:120]}")
            ok = False
        bad += not ok
        print(f"{'OK  ' if ok else 'FAIL'} {ats}:{ident:<45} postings={len(res.jobs)} {'; '.join(res.errors[:1])}")
    print(f"\n{len(conn.boards) - bad} of {len(conn.boards)} boards reachable. Remove failing lines from config/targets.yaml.")
    return 0 if not bad else 1


def cmd_run(args) -> int:
    import dataclasses
    settings = load_settings()
    if args.dry_run:
        settings = dataclasses.replace(settings, dry_run=True)
    buffer = setup_logging(settings.log_level, settings.secret_values())
    problems = [p for p in settings.validate(need_sheets=True, need_email=False) if "Apify" not in p]
    if problems:
        for p in problems:
            print("CONFIG PROBLEM:", p)
        return 2
    from integrations.google_sheets import GoogleSheetsStore
    from processing.ai_assist import GeminiAssistant
    from processing.pipeline import run_pipeline
    http = HttpClient(settings.http_timeout_seconds, settings.http_max_retries, settings.request_delay_seconds)
    ai = GeminiAssistant(settings.gemini_api_key, settings.gemini_model, settings.gemini_max_calls_per_run, http) \
        if settings.gemini_enabled and settings.gemini_api_key else None
    try:
        store = GoogleSheetsStore(settings)
        store.connect()
        out = run_pipeline(settings, store, _connectors(settings, http), log_buffer=buffer, ai=ai)
    except Exception as exc:  # noqa: BLE001
        from integrations.google_sheets import explain_error
        print("RUN FAILED:", explain_error(exc))
        return 1
    _print_outcome(out)
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    d = sub.add_parser("demo")
    d.add_argument("--out", default=str(PROJECT_ROOT / "demo_output"))
    sub.add_parser("check")
    sub.add_parser("validate-targets")
    r = sub.add_parser("run")
    r.add_argument("--dry-run", action="store_true")
    args = ap.parse_args(argv)
    return {"demo": cmd_demo, "check": cmd_check, "validate-targets": cmd_validate_targets, "run": cmd_run}[args.cmd](args)


if __name__ == "__main__":
    sys.exit(main())
