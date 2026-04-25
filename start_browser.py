#!/usr/bin/env python3
"""Cross-platform launcher: starts one Chrome/Chromium instance per USCIS account."""

import platform
import shutil
import socket
import subprocess
import sys
import time
from pathlib import Path

import yaml


def port_in_use(port: int) -> bool:
    """Return True if something is already listening on the given local port."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(0.5)
        return s.connect_ex(("127.0.0.1", port)) == 0


def find_browser() -> str:
    """Return the path/name of an available Chrome or Chromium executable."""
    system = platform.system()

    if system == "Windows":
        candidates = [
            r"C:\Program Files\Google\Chrome\Application\chrome.exe",
            r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
            r"C:\Program Files\Chromium\Application\chrome.exe",
        ]
        for c in candidates:
            if Path(c).exists():
                return c

    elif system == "Darwin":  # macOS
        candidates = [
            "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
            "/Applications/Chromium.app/Contents/MacOS/Chromium",
        ]
        for c in candidates:
            if Path(c).exists():
                return c

    else:  # Linux
        for name in ("chromium-browser", "chromium", "google-chrome", "google-chrome-stable"):
            if shutil.which(name):
                return name

    return ""


def main():
    config_path = Path(__file__).parent / "config.yaml"
    if not config_path.exists():
        print("config.yaml not found. Copy config.yaml.example to config.yaml and fill in your details.")
        sys.exit(1)

    with open(config_path) as f:
        config = yaml.safe_load(f)

    browser = find_browser()
    if not browser:
        print("No supported browser found. Install Google Chrome or Chromium.")
        print("  Linux:   sudo apt install chromium-browser")
        print("  macOS:   https://www.google.com/chrome/")
        print("  Windows: https://www.google.com/chrome/")
        sys.exit(1)

    print(f"Using browser: {browser}")

    accounts = config.get("uscis", {}).get("accounts", [])
    if not accounts:
        print("No accounts found in config.yaml.")
        sys.exit(1)

    for account in accounts:
        name = account.get("name", "Account")
        port = account.get("browser_port", 9222)
        profile = account.get("browser_profile_path", f"./browser-profile-{port}")
        profile_path = Path(profile).expanduser().resolve()

        if port_in_use(port):
            print(f"  {name}: port {port} already in use — skipping (browser likely already running)")
            continue

        profile_path.mkdir(parents=True, exist_ok=True)
        cmd = [
            browser,
            f"--remote-debugging-port={port}",
            f"--user-data-dir={profile_path}",
            "--no-first-run",
        ]
        subprocess.Popen(cmd)
        print(f"  {name}: started on port {port}, profile: {profile_path}")
        time.sleep(2)

    print("\nBrowsers are running. Log in to your USCIS accounts in each window.")
    print("Then run: python tracker.py")


if __name__ == "__main__":
    main()
