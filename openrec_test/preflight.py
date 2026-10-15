"""Check optional dependencies before starting or mutating a deployment."""
import sys


def main():
    import pytest  # noqa: F401
    if any(name in sys.argv for name in ("ui", "models-ui")):
        from playwright.sync_api import sync_playwright
        with sync_playwright() as engine:
            browser = engine.chromium.launch(headless=True)
            browser.close()
    print("Test dependencies ready")


if __name__ == "__main__":
    main()
