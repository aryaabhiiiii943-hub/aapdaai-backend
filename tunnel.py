"""Open the tunnel without depending on ngrok being on PATH.

WHY THIS EXISTS
    A Microsoft Store execution alias was shadowing ngrok with a stub pointing
    at a package that wasn't installed. Reinstalling didn't clear it. pyngrok
    downloads and manages its own copy of the binary, so PATH, aliases and
    installers stop being part of the problem.

    pip install pyngrok

    python tunnel.py

Leave it running. It prints the public URL and holds the tunnel open until you
press Ctrl+C.
"""
from __future__ import annotations

import os
import sys

# Optional: pyngrok may be installed outside the venv, where python-dotenv
# isn't. Reading .env is a convenience here, not a requirement.
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

PORT = 8000


def main() -> None:
    try:
        from pyngrok import conf, ngrok
    except ImportError:
        sys.exit("pyngrok not installed.  Run:  pip install pyngrok")

    token = os.environ.get("NGROK_AUTHTOKEN", "").strip()
    if token:
        ngrok.set_auth_token(token)
    else:
        # pyngrok also reads an existing ngrok.yml, so a previously configured
        # machine works with no token here at all.
        print("[tunnel] no NGROK_AUTHTOKEN in .env - relying on ngrok.yml")

    domain = os.environ.get("NGROK_DOMAIN", "").strip()
    options = {"bind_tls": True}
    if domain:
        options["domain"] = domain     # a reserved domain keeps the URL stable

    try:
        tunnel = ngrok.connect(PORT, **options)
    except Exception as err:           # noqa: BLE001
        sys.exit(f"[tunnel] failed: {type(err).__name__}: {err}")

    print()
    print("  public URL :", tunnel.public_url)
    print("  webhook    :", tunnel.public_url.rstrip("/") + "/webhook")
    print("  inspector  : http://127.0.0.1:4040")
    print()
    print("  Leave this window open. Ctrl+C to stop.")
    print()

    try:
        ngrok.get_ngrok_process().proc.wait()
    except KeyboardInterrupt:
        print("\n[tunnel] closing")
        ngrok.kill()


if __name__ == "__main__":
    main()
