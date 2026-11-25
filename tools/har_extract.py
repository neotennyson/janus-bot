#!/usr/bin/env python3
"""Extract UNIVEST API calls from a browser/app HAR capture.

Usage:
    python tools/har_extract.py capture.har --host univest --out data/captured

Writes one JSON file per interesting API response so the API adapter can be
written against the real schema, plus a summary of every endpoint seen.
"""
from __future__ import annotations

import argparse
import json
import re
from pathlib import Path


def load(har_path: str) -> dict:
    return json.loads(Path(har_path).read_text())


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("har")
    ap.add_argument("--host", default="univest")
    ap.add_argument("--out", default="data/captured")
    args = ap.parse_args()

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)

    entries = load(args.har).get("log", {}).get("entries", [])
    summary = []
    n = 0
    for e in entries:
        req = e.get("request", {})
        url = req.get("url", "")
        if args.host.lower() not in url.lower():
            continue
        method = req.get("method", "GET")
        resp = e.get("response", {})
        content = resp.get("content", {}) or {}
        mime = content.get("mimeType", "")
        text = content.get("text", "")
        summary.append({"method": method, "url": url, "status": resp.get("status"), "mime": mime})
        if "json" in mime and text:
            n += 1
            safe = re.sub(r"[^A-Za-z0-9]+", "_", url.split("?")[0])[-80:]
            (out / f"{n:03d}_{safe}.json").write_text(text)

    (out / "endpoints.json").write_text(json.dumps(summary, indent=2))
    print(f"found {len(summary)} calls to host '{args.host}'; wrote {n} JSON bodies to {out}/")
    print(f"endpoint summary -> {out}/endpoints.json")


if __name__ == "__main__":
    main()
