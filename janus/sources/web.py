from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import List, Optional

from ..models import Signal
from ..univest_parse import signals_from_payload

log = logging.getLogger("janus.web")


class UnivestWeb:
    """Drives a logged-in browser session against univest.in.

    login()    -> open a headed browser, you log in once (phone + OTP); the
                  session is saved to state_path and reused afterwards.
    discover() -> browse the Ideas page while recording every JSON response to
                  captured_dir, so the private web API can be pinned down.
    poll()     -> reuse the saved session, pull the ideas, return Signals.
    """

    def __init__(self, base_url: str = "https://univest.in",
                 ideas_path: str = "/intraday-stocks-for-today",
                 state_path: str = "data/univest_state.json",
                 captured_dir: str = "data/captured",
                 api_hint: str = ""):
        self.base_url = base_url.rstrip("/")
        self.ideas_path = ideas_path
        self.state_path = Path(state_path)
        self.captured_dir = Path(captured_dir)
        self.captured_dir.mkdir(parents=True, exist_ok=True)
        self.api_hint = api_hint
        self._found: List[Signal] = []

    # -- internals ----------------------------------------------------------
    @staticmethod
    def _require_playwright():
        try:
            from playwright.sync_api import sync_playwright  # noqa: F401
            return sync_playwright
        except ImportError as e:
            raise RuntimeError(
                "playwright not installed. Run: pip install playwright && playwright install chromium"
            ) from e

    def _context(self, pw, headless: bool):
        browser = pw.chromium.launch(headless=headless)
        kwargs = {}
        if self.state_path.exists():
            kwargs["storage_state"] = str(self.state_path)
        ctx = browser.new_context(**kwargs)
        return browser, ctx

    # -- public API ---------------------------------------------------------
    def login(self, wait_seconds: int = 240):
        sync_playwright = self._require_playwright()
        with sync_playwright() as pw:
            browser, ctx = self._context(pw, headless=False)
            page = ctx.new_page()
            page.goto(f"{self.base_url}/user/log-in")
            print(">> Log in in the opened browser (phone + OTP). Waiting...")
            page.wait_for_timeout(wait_seconds * 1000)
            ctx.storage_state(path=str(self.state_path))
            log.info("session saved to %s", self.state_path)
            browser.close()

    def discover(self, seconds: int = 40) -> Path:
        sync_playwright = self._require_playwright()
        captured: List[dict] = []
        with sync_playwright() as pw:
            browser, ctx = self._context(pw, headless=False)
            page = ctx.new_page()

            def on_response(resp):
                url = resp.url
                if "univest" not in url:
                    return
                ctype = (resp.headers or {}).get("content-type", "")
                if "json" not in ctype:
                    return
                try:
                    body = resp.json()
                except Exception:
                    return
                captured.append({"url": url, "status": resp.status, "body": body})

            page.on("response", on_response)
            page.goto(f"{self.base_url}{self.ideas_path}")
            page.wait_for_timeout(seconds * 1000)

            (self.captured_dir / "web_responses.json").write_text(json.dumps(captured, indent=2))
            lines = [f"{len(captured)} JSON responses recorded", ""]
            for c in captured:
                body = json.dumps(c["body"])[:200]
                lines.append(f"{c['status']} {c['url']}\n    {body}\n")
            digest = "\n".join(lines)
            (self.captured_dir / "web_endpoints.txt").write_text(digest)
            print(digest)
            browser.close()
        return self.captured_dir / "web_responses.json"

    def poll(self) -> List[Signal]:
        sync_playwright = self._require_playwright()
        found: List[dict] = []
        with sync_playwright() as pw:
            browser, ctx = self._context(pw, headless=True)
            page = ctx.new_page()

            def on_response(resp):
                url = resp.url
                if "univest" not in url or (self.api_hint and self.api_hint not in url):
                    return
                ctype = (resp.headers or {}).get("content-type", "")
                if "json" not in ctype:
                    return
                try:
                    found.append(resp.json())
                except Exception:
                    pass

            page.on("response", on_response)
            page.goto(f"{self.base_url}{self.ideas_path}", wait_until="networkidle")
            page.wait_for_timeout(4000)
            html = page.content()
            browser.close()

        signals: List[Signal] = []
        seen = set()
        for payload in found:
            for sig in signals_from_payload(payload, source="univest-web"):
                if sig.key not in seen:
                    seen.add(sig.key)
                    signals.append(sig)
        # fall back to parsing the rendered page text for any cards the API missed
        for sig in self._signals_from_html(html):
            if sig.key not in seen:
                seen.add(sig.key)
                signals.append(sig)
        return signals

    def _signals_from_html(self, html: str) -> List[Signal]:
        from ..normalize import from_text
        import re
        text = re.sub(r"<[^>]+>", " ", html)
        text = re.sub(r"\s+", " ", text)
        out = []
        for chunk in re.split(r"(?=(?:BUY|SELL)\s)", text):
            if "SL" in chunk and ("TGT" in chunk or "TARGET" in chunk):
                sig = from_text(chunk[:300], source="univest-html")
                if sig:
                    out.append(sig)
        return out
