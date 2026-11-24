# Contributing

1. Fork, create a branch, `pip install -r requirements-dev.txt`, make changes, run `pytest -q`.
2. Rule changes need a test in `tests/test_rules.py`. New connectors need a parser test on a fixture and must never raise out of `fetch`.
3. Never commit secrets, `.env`, or credential files. Never add features that apply to jobs automatically, bypass CAPTCHAs/logins, or ignore `robots.txt`.
4. Do not claim a live integration works unless you ran it; say so in the PR.
