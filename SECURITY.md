# Security policy

## Reporting a vulnerability

Report privately through
[GitHub Security Advisories](https://github.com/chukfinley/gscrape/security/advisories/new).
Please do not open a public issue for anything exploitable.

Expect an initial response within a week.

## Scope

This library makes HTTP requests and parses untrusted responses. Relevant
classes of issue include:

- code execution or path traversal triggered by a crafted Google response;
- credential leakage — cookie jars are written to `~/.cache/gscrape/` with mode
  `0600`; a bug that widens those permissions or writes them elsewhere counts;
- proxy credentials appearing in logs, exception messages or exports.

Out of scope: Google blocking you, rate limits, captchas, and the Terms of
Service question — see the README.

## Handling your own data

- Cookie jars authenticate *you*. Do not commit them, and do not share them:
  they are bound to your egress IP and identify your session.
- Proxy credentials belong in `~/.config/gscrape/proxies.txt` or
  `$GSCRAPE_PROXIES`, never in source.
- Exported results may contain personal data (reviews carry author names). If
  you publish or store them, that is your responsibility under whichever data
  protection law applies to you.
