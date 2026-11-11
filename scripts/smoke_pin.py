"""Real Chromium keyboard/touch timing, isolated from the normal bank database."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time

import httpx
from playwright.sync_api import expect, sync_playwright

ROOT = Path(__file__).resolve().parent.parent
ARTIFACTS = ROOT / ".runtime/artifacts"
BASE = "http://127.0.0.1:8002" + os.environ.get("PURPLE_SMOKE_PREFIX", "").rstrip("/")


def enter_pin(page, source, gap=200, digits="246810", cdp=None):
    for index, digit in enumerate(digits):
        if source == "keyboard":
            page.keyboard.down(digit)
            page.wait_for_timeout(110)
            page.keyboard.up(digit)
        else:
            button = page.locator(f'[data-digit="{digit}"]')
            button.scroll_into_view_if_needed()
            bounds = button.bounding_box()
            cdp.send("Input.dispatchTouchEvent", {"type": "touchStart", "touchPoints": [
                {"x": bounds["x"] + bounds["width"] / 2, "y": bounds["y"] + bounds["height"] / 2}]})
            page.wait_for_timeout(110)
            cdp.send("Input.dispatchTouchEvent", {"type": "touchEnd", "touchPoints": []})
        if index != len(digits) - 1:
            page.wait_for_timeout(gap)


def main():
    ARTIFACTS.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="purple-pin-") as directory, httpx.Client(trust_env=False) as http:
        shutil.copytree(ROOT / ".runtime/ml", Path(directory) / "ml")
        try:
            http.get(BASE + "/api/health", timeout=0.3)
        except httpx.ConnectError:
            pass
        else:
            raise RuntimeError("Port 8002 is occupied; do not stop an unrelated server")
        with (ARTIFACTS / "pin-browser-server.log").open("w") as log:
            process = subprocess.Popen([sys.executable, "-m", "uvicorn", "server.app:app", "--port", "8002"],
                                       cwd=ROOT, env={**os.environ, "PURPLE_DATA_DIR": directory}, stdout=log, stderr=log)
        try:
            for _ in range(150):
                if process.poll() is not None:
                    raise RuntimeError("PIN smoke server failed to start")
                try:
                    response = http.get(BASE + "/api/health", timeout=1)
                    response.raise_for_status()
                    break
                except httpx.ConnectError:
                    time.sleep(0.1)
            else:
                raise RuntimeError("PIN smoke server startup timeout")
            with sync_playwright() as playwright:
                options = {"headless": True, "args": ["--no-sandbox"]}
                if os.environ.get("CHROMIUM_PATH"):
                    options["executable_path"] = os.environ["CHROMIUM_PATH"]
                browser = playwright.chromium.launch(**options)
                checks = []
                for source in ("keyboard", "pointer"):
                    mobile = source == "pointer"
                    context = browser.new_context(viewport={"width": 390 if mobile else 1360, "height": 844 if mobile else 1000},
                                                  is_mobile=mobile, has_touch=mobile)
                    page = context.new_page()
                    errors = []
                    page.on("pageerror", lambda error: errors.append(str(error)))
                    page.goto(BASE)
                    page.get_by_role("link", name="PIN rhythm practice · Reference data").click()
                    expect(page.locator("#pin-title")).to_be_visible()
                    cdp = context.new_cdp_session(page) if mobile else None
                    for model in ("isolation_forest", "one_class_svm"):
                        page.select_option("#pin-model", model)
                        for gap, tier in ((200, "allow"), (1000, "block")):
                            page.locator("#pin-reset").click()
                            enter_pin(page, source, gap, cdp=cdp)
                            expect(page.locator("#pin-submit")).to_be_enabled()
                            with page.expect_response(lambda r: r.url.endswith("/api/demo/pin-rhythm") and r.request.method == "POST") as pending:
                                page.locator("#pin-submit").click()
                            response = pending.value
                            assert response.ok, response.text()
                            result = response.json()
                            sent = json.loads(response.request.post_data)
                            assert set(sent) == {"strokes", "source", "model"}
                            assert all(set(s) == {"down_ms", "up_ms"} for s in sent["strokes"])
                            assert result["tier"] == tier, result
                            assert result["input_source"] == source
                            assert result["identity_verified"] is False
                            expect(page.locator("#pin-result")).to_contain_text("the operator’s identity is not verified")
                            assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
                            checks.append({"input": source, "model": model, "gap_target_ms": gap,
                                           "tier": result["tier"], "measured": result["features_measured"]})
                            page.screenshot(path=str(ARTIFACTS / f"pin-demo-{'mobile' if mobile else 'desktop'}-{tier}.png"), full_page=True)
                    # A network failure never invents a result and can be retried
                    # with exactly the measured sample, without duplicate input.
                    page.locator("#pin-reset").click()
                    enter_pin(page, source, cdp=cdp)
                    page.route("**/api/demo/pin-rhythm", lambda route: route.abort())
                    page.locator("#pin-submit").click()
                    expect(page.locator("#pin-error")).not_to_be_empty()
                    expect(page.locator("#pin-result")).to_be_empty()
                    expect(page.locator("#pin-submit")).to_be_enabled()
                    page.unroute("**/api/demo/pin-rhythm")
                    with page.expect_response(lambda r: r.url.endswith("/api/demo/pin-rhythm") and r.request.method == "POST") as retry:
                        page.locator("#pin-submit").click()
                    assert retry.value.json()["tier"] == "allow"
                    expect(page.locator("#pin-result")).to_contain_text("Rhythm matches the reference")
                    # Wrong and incomplete PIN do not receive an invented score.
                    page.locator("#pin-reset").click()
                    enter_pin(page, source, digits="246811", cdp=cdp)
                    page.locator("#pin-submit").click()
                    expect(page.locator("#pin-error")).to_contain_text("Practice PIN does not match")
                    expect(page.locator("#pin-result")).to_be_empty()
                    enter_pin(page, source, digits="24", cdp=cdp)
                    expect(page.locator("#pin-submit")).to_be_disabled()
                    page.evaluate("window.dispatchEvent(new Event('blur'))")
                    expect(page.locator("#pin-progress")).to_have_text("0 / 6 digits recorded")
                    assert not errors, errors
                    context.close()
                browser.close()
                print(json.dumps({"browser_checks": checks, "wrong_pin_incomplete_focus_reset_network_retry": "passed"}, indent=2))
        finally:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()


if __name__ == "__main__":
    main()
