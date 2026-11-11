"""Real-browser presentation/three-tier checks against an isolated bank database.

The browser drives the UI. Read-only API checks verify balances and actual model
metadata instead of treating a toast or an open port as proof of correct behaviour.
"""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.request

from playwright.sync_api import expect, sync_playwright

ROOT = Path(__file__).resolve().parent.parent
ARTIFACTS = ROOT / ".runtime/artifacts"
ARTIFACTS.mkdir(parents=True, exist_ok=True)
BASE = "http://127.0.0.1:8001" + os.environ.get("PURPLE_SMOKE_PREFIX", "").rstrip("/")


def api(page, path):
    response = page.request.get(BASE + "/api" + path)
    assert response.ok, f"GET {path}: {response.status}"
    return response.json()


def balance(page):
    return api(page, "/overview")["user"]["balance_minor"]


def navigate(page, target):
    page.locator(f'nav [data-page="{target}"]').click()


def close_result(page):
    expect(page.locator("#result-dialog")).to_be_visible()
    page.locator("#dialog-close").click()
    expect(page.locator("#result-dialog")).not_to_be_visible()


def scenario(page, name):
    with page.expect_response(lambda r: r.url.endswith("/api/demo/scenario") and r.request.method == "POST") as pending:
        page.locator(f'[data-scenario="{name}"]').click()
    response = pending.value
    assert response.ok, f"Scenario {name}: {response.status} {response.text()}"
    return response.json()


def screenshot(page, filename):
    page.screenshot(path=str(ARTIFACTS / filename), full_page=True)


with tempfile.TemporaryDirectory(prefix="purple-browser-") as directory:
    shutil.copytree(ROOT / ".runtime/ml", Path(directory) / "ml")
    # Check before launch: a pre-existing unrelated server must never validate us.
    try:
        urllib.request.urlopen(BASE + "/api/health", timeout=0.3)
    except OSError:
        pass
    else:
        raise RuntimeError("Port 8001 is occupied; stop only a known test process before retrying")
    env = {**os.environ, "PURPLE_DATA_DIR": directory}
    with (ARTIFACTS / "browser-server.log").open("w") as log:
        process = subprocess.Popen([sys.executable, "-m", "uvicorn", "server.app:app", "--port", "8001"],
                                   cwd=ROOT, env=env, stdout=log, stderr=log)
    try:
        for _ in range(150):
            if process.poll() is not None:
                raise RuntimeError("Browser test server failed to start; see .runtime/artifacts/browser-server.log")
            try:
                health = json.load(urllib.request.urlopen(BASE + "/api/health", timeout=1))
                assert health["model"] == "purple-oneclass-v2", health
                break
            except OSError:
                time.sleep(0.1)
        else:
            raise RuntimeError("Browser test server not ready")
        with sync_playwright() as playwright:
            options = {"headless": True, "args": ["--no-sandbox"]}
            executable = os.environ.get("CHROMIUM_PATH") or shutil.which("chromium") or shutil.which("chromium-browser")
            if executable:
                options["executable_path"] = executable
            # Otherwise use Playwright's installed headless shell, like the PIN
            # smoke test; do not assume /usr/bin/chromium exists on every host.
            browser = playwright.chromium.launch(**options)
            page = browser.new_page(viewport={"width": 1440, "height": 1000})
            errors = []
            page.on("pageerror", lambda error: errors.append(str(error)))
            page.on("dialog", lambda dialog: dialog.accept("Demo identity reviewed for research presentation"))
            page.goto(BASE)
            page.locator("#demo-customer").click()
            expect(page.locator("nav [data-page=overview]")).to_be_visible()
            expect(page.locator(".balance")).to_contain_text("12,500.00")
            screenshot(page, "dashboard-desktop.png")
            initial = balance(page)

            metadata = api(page, "/model")
            assert "synthetic" in str(metadata["data_source"]).lower()
            profile = metadata["users"]["demo-irfan"]
            assert set(profile["models"]) == {"isolation_forest", "one_class_svm"}
            for model in profile["models"].values():
                for metric in ("eer", "far", "frr", "roc_auc"):
                    assert 0 <= model[metric] <= 1
                assert model["thresholds"]["review"] < model["thresholds"]["block"]
                assert sum(map(sum, model["confusion_matrix"])) == model["test_rows"]

            navigate(page, "presentation")
            expect(page.locator(".presentation-heading")).to_contain_text("SUPERVISOR")
            expect(page.locator("[data-slide]")).to_have_count(6)
            narrative = []
            for index in range(6):
                page.locator(f'[data-slide="{index}"]').click()
                expect(page.locator(f'[data-slide="{index}"]')).to_have_class("active")
                narrative.append(page.locator(".content").inner_text())
            combined = " ".join(narrative)
            for term in ("Isolation Forest", "One-Class SVM", "genuine", "validation", "generated"):
                assert term.lower() in combined.lower(), f"Missing presentation explanation: {term}"
            page.locator('[data-slide="2"]').click()
            screenshot(page, "presentation-page.png")
            navigate(page, "lab")
            for term in ("EER", "FAR", "FRR", "ROC"):
                assert term in page.locator(".content").inner_text()
            screenshot(page, "model-comparison.png")

            navigate(page, "security")
            normal = scenario(page, "normal")
            assert normal["transaction"]["status"] == "completed"
            assert normal["transaction"]["decision_details"]["input_source"] == "curated_synthetic_replay"
            close_result(page)
            after_normal = balance(page)
            assert after_normal == initial - normal["transaction"]["amount_minor"]

            medium = scenario(page, "medium")
            assert medium["status"] == "step_up_required"
            assert medium["transaction"]["status"] == "step_up_required"
            assert balance(page) == after_normal, "Medium risk must hold, not debit"
            screenshot(page, "step-up-held.png")
            with page.expect_response(lambda r: "/api/step-up/" in r.url and r.url.endswith("/verify") and r.request.method == "POST") as verified:
                page.locator("#approve-stepup").click()
            verification = verified.value
            assert verification.ok, verification.text()
            assert verification.json()["transaction"]["status"] == "completed"
            close_result(page)
            after_verified = balance(page)
            assert after_verified == after_normal - medium["transaction"]["amount_minor"]
            # The very same session-bound challenge cannot move money twice.
            replay = page.request.post(BASE + f"/api/step-up/{medium['challenge_id']}/verify", data={"method": "demo", "approved": True})
            assert replay.status in {409, 410}
            assert balance(page) == after_verified

            page.locator("[data-model-select]").select_option("one_class_svm")
            svm_normal = scenario(page, "normal")
            assert svm_normal["transaction"]["decision_details"]["model_name"] == "one_class_svm"
            assert svm_normal["transaction"]["status"] == "completed"
            close_result(page)
            after_verified -= svm_normal["transaction"]["amount_minor"]
            assert balance(page) == after_verified
            page.locator("[data-model-select]").select_option("isolation_forest")

            # Ordinary web input does not contain all seven biometric modalities.
            # It must hold safely and display unscored/null as "Not assessed",
            # not silently invent a full profile or a 0% anomaly percentile.
            navigate(page, "overview")
            page.locator("#recipient").select_option("demo-sarah")
            page.locator("#amount").fill("123.45")
            with page.expect_response(lambda r: r.url.endswith("/api/transfers") and r.request.method == "POST") as held:
                page.locator("#transfer-form button[type=submit]").click()
            unscored_response = held.value
            assert unscored_response.ok, unscored_response.text()
            unscored = unscored_response.json()
            assert unscored["status"] == "step_up_required"
            assert unscored["transaction"]["decision_details"]["score_available"] is False
            assert unscored["transaction"]["model_score"] is None
            assert unscored["transaction"]["risk_score"] is None
            assert balance(page) == after_verified
            expect(page.locator("#result-dialog")).to_be_visible()
            expect(page.locator("#dialog-content")).to_contain_text("Not assessed")
            assert page.locator("#dialog-content").inner_text().count("Not assessed") == 1
            screenshot(page, "unscored-held.png")
            with page.expect_response(lambda r: "/api/step-up/" in r.url and r.url.endswith("/verify") and r.request.method == "POST") as cancelled:
                page.locator("#cancel-stepup").click()
            cancel_response = cancelled.value
            assert cancel_response.ok, cancel_response.text()
            assert cancel_response.json()["transaction"]["status"] == "cancelled"
            close_result(page)
            assert balance(page) == after_verified, "Cancelling an unscored hold must not debit"
            navigate(page, "security")

            high = scenario(page, "high")
            assert high["transaction"]["status"] == "blocked"
            assert high["account_status"] == "locked"
            close_result(page)
            assert balance(page) == after_verified
            expect(page.locator(".notice.locked")).to_be_visible()
            screenshot(page, "security-blocked.png")
            navigate(page, "overview")
            expect(page.locator("#transfer-form button[type=submit]")).to_be_disabled()
            page.set_viewport_size({"width": 390, "height": 844})
            screenshot(page, "dashboard-mobile.png")
            overflow = page.evaluate("""() => ({width: innerWidth, document: document.documentElement.scrollWidth,
                elements: Array.from(document.querySelectorAll('.content *')).filter(e => e.getBoundingClientRect().right > innerWidth + 1)
                .slice(0, 12).map(e => ({tag:e.tagName, class:e.className, right:e.getBoundingClientRect().right}))})""")
            assert overflow["document"] <= overflow["width"], f"Customer mobile page overflows: {overflow}"

            page.locator(".topbar [data-logout]").click()
            page.locator("#demo-admin").click()
            expect(page.locator("nav [data-page=admin]")).to_be_visible()
            page.set_viewport_size({"width": 1440, "height": 1000})
            expect(page.locator("[data-unlock]")).to_have_count(1)
            page.locator("[data-unlock]").click()
            expect(page.locator("[data-unlock]")).to_have_count(0)
            screenshot(page, "admin-review.png")
            navigate(page, "presentation")
            expect(page.locator(".presentation-heading")).to_contain_text("SUPERVISOR")
            page.locator(".topbar [data-logout]").click()
            page.locator("#demo-customer").click()
            expect(page.locator("nav [data-page=overview]")).to_be_visible()
            assert balance(page) == after_verified
            expect(page.locator("#transfer-form button[type=submit]")).to_be_enabled()
            page.set_viewport_size({"width": 390, "height": 844})
            navigate(page, "lab")
            assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth"), "Mobile model comparison overflows"
            navigate(page, "presentation")
            page.locator('[data-slide="2"]').click()
            assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth"), "Mobile presentation overflows"
            screenshot(page, "presentation-mobile.png")

            # A newly registered account must offer an actual route to the
            # no-enrolment exercise while keeping its bank transfer unscored.
            # Drive the real form, not a fabricated modal/result fixture.
            page.locator(".topbar [data-logout]").click()
            page.locator("#auth-switch").click()
            page.locator("#full-name").fill("Rhythm Practice Account")
            page.locator("#email").fill("rhythm-practice@example.test")
            page.locator("#password").fill("PrivatePractice!2026")
            page.locator("#auth-form button[type=submit]").click()
            expect(page.locator("#full-name")).to_have_count(0)
            page.locator("#email").fill("rhythm-practice@example.test")
            page.locator("#password").fill("PrivatePractice!2026")
            page.locator("#auth-form button[type=submit]").click()
            expect(page.locator("nav [data-page=overview]")).to_be_visible()
            expect(page.locator('.notice a[href$="/pin-demo"]')).to_have_attribute("href", BASE.removeprefix("http://127.0.0.1:8001") + "/pin-demo")
            before_practice = api(page, "/overview")
            page.locator("#recipient").select_option("demo-sarah")
            page.locator("#amount").fill("1.00")
            with page.expect_response(lambda r: r.url.endswith("/api/transfers") and r.request.method == "POST") as missing_profile:
                page.locator("#transfer-form button[type=submit]").click()
            missing = missing_profile.value.json()
            assert missing["status"] == "enrollment_required"
            assert missing["transaction"]["decision_details"]["score_available"] is False
            assert missing["transaction"]["model_score"] is None
            assert "challenge_id" not in missing
            after_refusal = api(page, "/overview")
            assert after_refusal["user"]["balance_minor"] == before_practice["user"]["balance_minor"]
            expect(page.locator("#dialog-content")).to_contain_text("246810, not your transaction PIN")
            expect(page.locator("#dialog-content")).to_contain_text("does not verify identity")
            screenshot(page, "enrollment-pin-practice-mobile.png")
            page.locator("#open-pin-demo").click()
            expect(page).to_have_url(BASE + "/pin-demo")
            expect(page.locator("#pin-title")).to_have_text("Enter the practice PIN")
            # Opening the exercise must not enrol a profile or settle the refusal.
            after_practice = api(page, "/overview")
            assert after_practice == after_refusal
            assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth"), "Mobile PIN exercise overflows"
            assert not errors, errors
            browser.close()
            print("PASS: customer/admin browser login; per-user IF/SVM evaluation; SV presentation; genuine/validation/test explanations; normal debit; medium hold then one-use simulated verification; missing-telemetry null scores rendered unscored and cancelled without debit; high block and unchanged balance; account lock/admin unlock; new-account enrolment refusal links to PIN practice without changing bank state; responsive mobile; zero JavaScript errors")
    finally:
        process.terminate()
        process.wait(timeout=15)
