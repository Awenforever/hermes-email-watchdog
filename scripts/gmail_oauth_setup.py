#!/usr/bin/env python3
"""One-time Gmail API authorization for the email watchdog.

Runs the installed-app flow without a browser on this host: print the URL, the
operator approves it on their own machine, then the redirect lands on localhost
and the whole URL (or just the code) is pasted back here.

Nothing is written until the exchange succeeds; the refresh token is stored 0600
next to the OAuth client JSON.
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import email_gmail_api as api


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--client-secret", default=str(api.client_secret_path()))
    ap.add_argument("--token", default=str(api.token_path()))
    ap.add_argument("--proxy", default=os.environ.get("EMAIL_GMAIL_API_PROXY", ""))
    ap.add_argument("--redirect-uri", default="http://localhost:8080")
    args = ap.parse_args(argv)

    secret = Path(args.client_secret)
    if not secret.is_file():
        print("MISSING_CLIENT_SECRET=%s" % secret)
        print("Create an OAuth client (Desktop app) in Google Cloud, enable the Gmail API,")
        print("then save the downloaded JSON at that path.")
        return 2

    api.apply_proxy_env(args.proxy)
    from google_auth_oauthlib.flow import InstalledAppFlow

    flow = InstalledAppFlow.from_client_secrets_file(str(secret), api.SCOPES)
    flow.redirect_uri = args.redirect_uri
    url, _state = flow.authorization_url(
        access_type="offline", include_granted_scopes="true", prompt="consent"
    )
    print("OPEN_THIS_URL=%s" % url)
    print("After approving, the browser lands on localhost (it may fail to load).")
    print("Paste the FULL redirected URL here:")
    pasted = sys.stdin.readline().strip()
    if not pasted:
        print("NO_INPUT")
        return 3
    code = pasted
    if "code=" in pasted:
        from urllib.parse import urlparse, parse_qs
        parsed = urlparse(pasted)
        code = (parse_qs(parsed.query).get("code") or [""])[0]
    if not code:
        print("NO_CODE_IN_URL")
        return 4
    flow.fetch_token(code=code)
    creds = flow.credentials
    api._write_private(Path(args.token), creds.to_json())
    print("TOKEN_WRITTEN=%s" % args.token)
    print("SCOPES=%s" % " ".join(api.SCOPES))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
