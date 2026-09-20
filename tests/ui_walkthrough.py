"""Drive the running Streamlit app through the paste-JSON path and screenshot it.

Needs the app already running:
    .venv/Scripts/python.exe -m streamlit run app.py --server.port 8577
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from playwright.sync_api import sync_playwright

from tests.fixtures import golden_vi as G

BASE = "http://localhost:8577/"
OUT = Path(__file__).resolve().parent.parent / "output" / "_ui"
TREND_URL = (
    "https://www.threads.com/search?q=Tuy%E1%BB%83n%20th%E1%BB%A7%20PUBG"
    "&serp_type=trends&trend_fbid=1072142712480212"
)


def payload() -> str:
    """Golden fixture shaped like a real Threads data-sjs payload."""
    return json.dumps(
        {"__bbox": {"result": {"data": {"data": {
            "edges": [{"node": {"thread_items": g}} for g in G.groups()]
        }}}}},
        ensure_ascii=False,
    )


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        page = browser.new_context(
            viewport={"width": 1600, "height": 1250}
        ).new_page()
        errors: list[str] = []
        page.on("pageerror", lambda e: errors.append(str(e)))

        page.goto(BASE, wait_until="networkidle", timeout=60_000)
        page.wait_for_timeout(3500)

        # --- step 1: pasted JSON -----------------------------------------
        # The URL field is skipped on purpose: pasting JSON is the escape hatch
        # for when the scraper (and therefore the link) is not usable at all, so
        # it has to work with an empty URL.
        #
        # Streamlit reruns on every committed widget and a rerun collapses the
        # expander, so the JSON goes in first and nothing else is touched after.
        page.locator("summary", has_text="Dán JSON thủ công").click()
        page.wait_for_selector("textarea[aria-label='JSON']", state="visible",
                               timeout=10_000)
        page.locator("textarea[aria-label='JSON']").fill(payload())
        page.keyboard.press("Control+Enter")
        page.wait_for_timeout(2500)
        page.screenshot(path=str(OUT / "step1_filled.png"))

        page.get_by_role("button", name="Lấy bình luận").click()
        page.wait_for_timeout(9000)
        page.screenshot(path=str(OUT / "step2_review.png"), full_page=True)

        text = page.inner_text("body")
        print("=== step 2 ===")
        for line in text.splitlines():
            if line.strip():
                print(" ", line[:110])

        ok = "Mạch truyện" in text
        print()
        print("reached review screen:", ok)
        print("js errors:", errors or "none")

        browser.close()
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
