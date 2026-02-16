#!/usr/bin/env python3
# FORJA_TEMPLATE_VERSION=0.1.0
"""Forja QA - Playwright browser testing helper.

Runs a suite of browser checks against a local server and produces
screenshots + a JSON report.

Usage:
    python3 .forja-tools/forja_qa_playwright.py [port] [screenshot_dir]
"""

import asyncio
import json
import subprocess
import sys
from pathlib import Path


async def run_qa(port=8080, screenshot_dir=".forja/screenshots"):
    """Run Playwright browser tests and return True if all pass."""
    try:
        from playwright.async_api import async_playwright
    except ImportError:
        print("Installing Playwright...")
        subprocess.run(
            [sys.executable, "-m", "pip", "install", "playwright", "-q"],
            check=True,
        )
        subprocess.run(
            [sys.executable, "-m", "playwright", "install", "chromium"],
            check=True,
        )
        from playwright.async_api import async_playwright

    Path(screenshot_dir).mkdir(parents=True, exist_ok=True)
    results = {"tests": [], "passed": 0, "failed": 0}
    base_url = f"http://localhost:{port}"

    async with async_playwright() as p:
        browser = await p.chromium.launch()

        # Test 1: Page loads
        page = await browser.new_page()
        try:
            response = await page.goto(base_url, timeout=10000)
            status = response.status if response else 0
            passed = status == 200
            await page.screenshot(path=f"{screenshot_dir}/page-load.png")
            results["tests"].append(
                {"name": "page_loads", "passed": passed, "status": status}
            )
        except Exception as e:
            results["tests"].append(
                {"name": "page_loads", "passed": False, "error": str(e)}
            )
        await page.close()

        # Test 2: Desktop viewport
        page = await browser.new_page(viewport={"width": 1280, "height": 720})
        try:
            await page.goto(base_url, timeout=10000)
            await page.screenshot(path=f"{screenshot_dir}/desktop.png")
            title = await page.title()
            has_content = await page.evaluate(
                "document.body.innerText.length > 10"
            )
            passed = bool(title) and has_content
            results["tests"].append(
                {"name": "desktop_viewport", "passed": passed}
            )
        except Exception as e:
            results["tests"].append(
                {"name": "desktop_viewport", "passed": False, "error": str(e)}
            )
        await page.close()

        # Test 3: Mobile viewport — no horizontal scroll
        page = await browser.new_page(viewport={"width": 375, "height": 812})
        try:
            await page.goto(base_url, timeout=10000)
            await page.screenshot(path=f"{screenshot_dir}/mobile.png")
            scroll_w = await page.evaluate(
                "document.documentElement.scrollWidth"
            )
            vp_w = await page.evaluate("window.innerWidth")
            passed = scroll_w <= vp_w + 5
            results["tests"].append(
                {
                    "name": "mobile_responsive",
                    "passed": passed,
                    "scroll_width": scroll_w,
                    "viewport": vp_w,
                }
            )
        except Exception as e:
            results["tests"].append(
                {"name": "mobile_responsive", "passed": False, "error": str(e)}
            )
        await page.close()

        # Test 4: No console errors
        page = await browser.new_page()
        errors: list[str] = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        try:
            await page.goto(base_url, timeout=10000)
            await page.wait_for_timeout(2000)
            passed = len(errors) == 0
            results["tests"].append(
                {
                    "name": "no_console_errors",
                    "passed": passed,
                    "errors": errors[:5],
                }
            )
        except Exception as e:
            results["tests"].append(
                {"name": "no_console_errors", "passed": False, "error": str(e)}
            )
        await page.close()

        # Test 5: Key elements exist (h1)
        page = await browser.new_page()
        try:
            await page.goto(base_url, timeout=10000)
            h1 = await page.query_selector("h1")
            nav = await page.query_selector("nav")
            has_links = await page.evaluate(
                "document.querySelectorAll('a').length > 0"
            )
            results["tests"].append(
                {
                    "name": "key_elements",
                    "passed": h1 is not None,
                    "h1": h1 is not None,
                    "nav": nav is not None,
                    "has_links": has_links,
                }
            )
        except Exception as e:
            results["tests"].append(
                {"name": "key_elements", "passed": False, "error": str(e)}
            )
        await page.close()

        await browser.close()

    results["passed"] = sum(1 for t in results["tests"] if t.get("passed"))
    results["failed"] = sum(
        1 for t in results["tests"] if not t.get("passed")
    )

    report_path = Path(screenshot_dir).parent / "qa-report.json"
    report_path.write_text(json.dumps(results, indent=2) + "\n")

    print(json.dumps(results, indent=2))
    return results["failed"] == 0


if __name__ == "__main__":
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8080
    sd = sys.argv[2] if len(sys.argv) > 2 else ".forja/screenshots"
    ok = asyncio.run(run_qa(port, sd))
    sys.exit(0 if ok else 1)
