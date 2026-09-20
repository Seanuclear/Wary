#!/bin/sh
# Mac and Linux: run ./preview.sh. Builds the site and opens it in your browser.
cd "$(dirname "$0")"
exec python3 preview.py
