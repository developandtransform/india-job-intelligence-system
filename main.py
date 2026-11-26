"""India Job Intelligence System - command line.

  python main.py demo      offline demo with invented sample jobs (no accounts needed)
  python main.py check     validate .env and Google Sheets access (no jobs fetched)
  python main.py validate-targets   test every ATS board in config/targets.yaml
  python main.py check-ai  send one tiny real request to the AI provider(s)
  python main.py discover-boards  verify candidate ATS boards (writes a CSV to review; edits nothing)
  python main.py registry-queue   companies to verify first, by number of relevant jobs
  python main.py show-config  print non-secret AI settings and the number of Apify tokens (never their values)
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
    if getattr(out, "blocked", None):
        print("\nDRY RUN STOPPED SAFELY - nothing was changed. The spreadsheet is not ready:")
        for b in out.blocked:
            print("  -", b)
        print("A dry run never repairs the sheet. Run `python main.py check` once (it creates missing tabs/headers), or do one production run, then retry.")
    if getattr(out, "skipped_ops", None):
        print("\nDry run - deliberately skipped:")
        for o in out.skipped_ops:
            print("  -", o)


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
    print("Apify tokens configured:", len(settings.apify_token_list), "(values never shown)")
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
    from processing.ai_assist import build_assistant
    from processing.pipeline import run_pipeline
    http = HttpClient(settings.http_timeout_seconds, settings.http_max_retries, settings.request_delay_seconds)
    # a dry run asks no AI by default (it may use free quota); DRY_RUN_ALLOW_AI=true opts in
    ai = None if (settings.dry_run and not settings.dry_run_allow_ai) else build_assistant(settings, http)
    try:
        store = GoogleSheetsStore(settings, read_only=settings.dry_run)
        store.connect()
        out = run_pipeline(settings, store, _connectors(settings, http), log_buffer=buffer, ai=ai)
    except Exception as exc:  # noqa: BLE001
        from integrations.google_sheets import explain_error
        print("RUN FAILED:", explain_error(exc))
        return 1
    _print_outcome(out)
    return 3 if out.blocked else 0


def _providers(settings, http):
    from integrations.ai_router import build_providers
    return build_providers(settings, http)


def _skip_reason(settings, name: str) -> str:
    cfg = settings.provider_config(name)
    if not cfg["enabled"]:
        return f"{name.upper()}_ENABLED=false"
    if not cfg["api_key"]:
        return "no API key" if name != "cloudflare" else "no CLOUDFLARE_API_TOKEN"
    if name == "cloudflare" and not cfg.get("account_id"):
        return "no CLOUDFLARE_ACCOUNT_ID"
    return "not in AI_PROVIDER_ORDER"


def cmd_show_config(args) -> int:
    """Print the non-secret AI settings that are really in effect, then how many Apify tokens are configured.

    Only the COUNT of Apify tokens is printed - never a token value, a prefix, a suffix or a length. Safe to show in
    GitHub Actions logs."""
    settings = load_settings()
    print("AI settings in effect (keys are never printed):")
    print(f"  AI_ENABLED        = {settings.ai_enabled}")
    print(f"  AI_PROVIDER_ORDER = {','.join(settings.ai_provider_order)}")
    print(f"  retries={settings.ai_max_retries} timeout={settings.ai_timeout_seconds:g}s")
    for name in ("groq", "gemini", "cloudflare", "openrouter", "custom"):
        c = settings.provider_config(name)
        key = f"key set ({len(c['api_key'])} chars)" if c["api_key"] else "key NOT set"
        extra = ""
        if name == "cloudflare":
            extra = f", account id {'set' if c['account_id'] else 'NOT set'}"
        print(f"  {name:<10} enabled={str(c['enabled']):<5} model={c['model']}  {key}{extra}  max_calls/run={c['max_calls']}")
    for p in settings.ai_problems():
        print("  PROBLEM:", p)
    print("Apify settings in effect (token values are never printed):")
    print(f"  Apify tokens configured = {len(settings.apify_token_list)}")
    return 0


def cmd_check_ai(args) -> int:
    """A real, tiny generation request to each enabled AI provider, one by one. Uses a few tokens of free quota."""
    from integrations.ai_providers import run_check
    settings = load_settings()
    setup_logging(settings.log_level, settings.secret_values())
    http = HttpClient(settings.http_timeout_seconds, settings.http_max_retries, 0.0)
    if not settings.ai_enabled:
        print("AI_ENABLED=false - AI is switched off. Nothing to check.")
        return 0
    providers = _providers(settings, http)
    active = {p.name for p in providers}
    for name in settings.ai_provider_order:
        if name not in active:
            print(f"[{name}] skipped ({_skip_reason(settings, name)})\n")
    if not providers:
        print("No AI provider is configured - the system runs with the Python rules only.")
        return 0
    failed = 0
    for p in providers:
        res = run_check(p)
        failed += not res.validated
        print(f"[{res.provider}]\n  Model            : {res.model}\n  Connected        : {'yes' if res.connected else 'NO'} ({res.status})"
              f"\n  Valid JSON reply : {'yes' if res.validated else 'NO'}\n  Retries          : {res.retries}"
              f"\n  Time             : {res.seconds:.1f}s\n  Result           : {'OK' if res.validated else res.reason}\n")
    print(f"{len(providers) - failed} of {len(providers)} provider(s) working. A failing provider is simply skipped in the daily run.")
    return 0 if not failed else 1


def cmd_notify_failure(args) -> int:
    """Called by GitHub Actions only when the daily run failed: sends one short e-mail. Never raises."""
    import os
    from integrations.email import send_email
    try:
        settings = load_settings()
        if not settings.email_enabled:
            print("E-mail is not configured; GitHub's own failure e-mail is the only notice.")
            return 0
        run_url = "{}/{}/actions/runs/{}".format(os.environ.get("GITHUB_SERVER_URL", "https://github.com"),
                                                os.environ.get("GITHUB_REPOSITORY", ""), os.environ.get("GITHUB_RUN_ID", ""))
        text = ("The daily job run FAILED before it finished, so no new digest was sent today.\n"
                f"Open the log: {run_url}\nNothing was deleted. Fix the problem (README section 20 / docs/TROUBLESHOOTING.md) and "
                "re-run the workflow from the Actions tab.")
        ok, msg = send_email(settings, "Job system: daily run FAILED", "<p>" + text.replace("\n", "<br>") + "</p>", text)
        print("Failure e-mail:", "sent" if ok else f"not sent ({msg})")
    except Exception as exc:  # noqa: BLE001
        print("Could not send the failure e-mail:", type(exc).__name__)
    return 0


def cmd_discover_boards(args) -> int:
    """Test candidate ATS boards for the employers in config/board_candidates.csv and write a CSV to review.
    Needs internet. Makes a few ordinary public requests per company, spaced by the normal delay. Edits nothing."""
    from pathlib import Path
    from connectors.ats_connector import AtsConnector
    from discovery.board_discovery import discover, load_candidates, write_report
    settings = load_settings()
    setup_logging(settings.log_level, settings.secret_values())
    http = HttpClient(settings.http_timeout_seconds, 1, max(settings.request_delay_seconds, 1.0))
    src = Path(args.file)
    if not src.exists():
        print(f"File not found: {src}")
        return 1
    cands = load_candidates(src)
    if args.limit:
        cands = cands[: args.limit]
    conn = AtsConnector(settings, http, boards=[])
    results = discover(cands, conn, progress=lambda r: print(f"  {r.company:<28} {r.ats or '-':<16} {r.identifier or '-':<24} {r.result}"))
    out = write_report(results, Path("discovery_output") / "discovered_boards.csv")
    found = [r for r in results if r.suggest]
    print(f"\n{len(found)} verified board(s) out of {len(cands)} employers. Review {out} and copy only the lines you want into "
          "config/targets.yaml (then run validate-targets).")
    return 0


def cmd_registry_queue(args) -> int:
    """Print the companies whose verification would unlock the most relevant jobs (reads your Sheet, changes nothing)."""
    from integrations.google_sheets import GoogleSheetsStore
    from processing.company_verification import CompanyRegistry
    from processing.monitoring import registry_queue
    from core.models import Job
    settings = load_settings()
    problems = settings.validate(need_sheets=True, need_email=False)
    if problems:
        print("Fix .env first:\n- " + "\n- ".join(problems))
        return 1
    store = GoogleSheetsStore(settings)
    store.connect()
    jobs = [Job.from_record(r) for r in store.load_master()]
    reg = CompanyRegistry()
    reg.load_sheet_rows(store.load_registry_rows())
    reg.load_seed()
    rows = registry_queue(jobs, reg, limit=args.limit)
    if not rows:
        print("Nothing waiting: every company behind a relevant job is verified.")
    for i, e in enumerate(rows, 1):
        print(f"{i:>2}. {e['company']:<40} {e['relevant_jobs']:>3} relevant job(s)   needs: {', '.join(e['missing'])}")
    print("\nFill these in the 'Company Registry' tab (Classification, Founded Year, Employee Count, Evidence URL).")
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    d = sub.add_parser("demo")
    d.add_argument("--out", default=str(PROJECT_ROOT / "demo_output"))
    sub.add_parser("check")
    sub.add_parser("validate-targets")
    sub.add_parser("check-ai")
    sub.add_parser("notify-failure")
    d = sub.add_parser("discover-boards")
    d.add_argument("--file", default="config/board_candidates.csv")
    d.add_argument("--limit", type=int, default=0)
    q = sub.add_parser("registry-queue")
    q.add_argument("--limit", type=int, default=25)
    sub.add_parser("show-config")
    r = sub.add_parser("run")
    r.add_argument("--dry-run", action="store_true")
    args = ap.parse_args(argv)
    return {"demo": cmd_demo, "check": cmd_check, "validate-targets": cmd_validate_targets, "run": cmd_run,
            "check-ai": cmd_check_ai, "notify-failure": cmd_notify_failure, "discover-boards": cmd_discover_boards, "registry-queue": cmd_registry_queue, "show-config": cmd_show_config}[args.cmd](args)


if __name__ == "__main__":
    sys.exit(main())
