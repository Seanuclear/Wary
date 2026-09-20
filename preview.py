#!/usr/bin/env python3
"""One-click local preview of the public site.

Builds the site, fetches the official feeds (if you are online), starts a small web server on your own
computer and opens your browser. Nothing is published. Press Ctrl+C in this window to stop it.

    Windows:      double-click preview.bat
    Mac / Linux:  ./preview.sh      (or: python3 preview.py)
"""
import argparse
import functools
import http.server
import os
import subprocess
import sys
import threading
import webbrowser

ROOT = os.path.dirname(os.path.abspath(__file__))


def run(script, *args):
    return subprocess.run([sys.executable, os.path.join(ROOT, "tools", script), *args], cwd=ROOT).returncode


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8770)
    ap.add_argument("--no-browser", action="store_true")
    a = ap.parse_args()

    print("Building the site...")
    if run("build_site.py") != 0:
        print("\nThe build stopped. The message above says what to fix (usually a line in editorial/signals.json).")
        return 1
    print("Fetching the official feeds (this is fine to skip if you are offline)...")
    if run("publish.py") != 0:
        print("\nPublishing stopped. The message above says what to fix.")
        return 1

    class Quiet(http.server.SimpleHTTPRequestHandler):
        def log_message(self, *args):  # keep the window quiet
            pass

    handler = functools.partial(Quiet, directory=os.path.join(ROOT, "site"))
    srv = None
    for port in range(a.port, a.port + 20):  # if the port is busy, try the next one
        try:
            srv = http.server.ThreadingHTTPServer(("127.0.0.1", port), handler)
            break
        except OSError:
            continue
    if srv is None:
        print("Could not find a free port. Close other programs and try again.")
        return 1
    url = f"http://127.0.0.1:{srv.server_address[1]}/"
    print(f"\nYour preview is running at {url}\nPress Ctrl+C in this window to stop it.")
    if not a.no_browser:
        threading.Timer(1.0, webbrowser.open, [url]).start()
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
