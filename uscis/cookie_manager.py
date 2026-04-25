"""Browser cookie extraction and session management for USCIS authentication."""

from pathlib import Path
from typing import Optional
from http.cookiejar import CookieJar

import browser_cookie3
import requests


class CookieManager:
    """Manages browser cookie extraction and session refresh for USCIS."""

    USCIS_DOMAIN = '.uscis.gov'

    def __init__(
        self,
        browser_type: str = 'chrome',
        profile_path: Optional[str] = None,
        auto_login=None  # AutoLogin instance, injected optionally
    ):
        """Initialize the cookie manager.

        Args:
            browser_type: Browser to extract cookies from ('chrome', 'chromium', or 'firefox')
            profile_path: Optional path to specific browser profile
            auto_login: Optional AutoLogin instance for automatic re-authentication
        """
        self.browser_type = browser_type.lower()
        self.profile_path = profile_path if profile_path else None
        self.auto_login = auto_login
        self._cookies: Optional[CookieJar] = None
        self._session = requests.Session()

    def _resolve_cookie_file(self, profile_path: str) -> str:
        """Resolve the actual SQLite cookie file from a profile directory path."""
        p = Path(profile_path)
        if p.is_dir():
            # Chromium/Chrome: <profile>/Default/Cookies
            candidate = p / "Default" / "Cookies"
            if candidate.exists():
                return str(candidate)
            # Firefox: <profile>/<random>.default/cookies.sqlite
            for f in p.rglob("cookies.sqlite"):
                return str(f)
            # Return as-is and let browser_cookie3 handle the error
        return profile_path

    def _extract_cookies(self) -> CookieJar:
        """Extract cookies from the configured browser's on-disk database."""
        if self.browser_type == 'chrome':
            fn = browser_cookie3.chrome
        elif self.browser_type == 'chromium':
            fn = browser_cookie3.chromium
        elif self.browser_type == 'firefox':
            fn = browser_cookie3.firefox
        else:
            raise ValueError(f"Unsupported browser type: {self.browser_type}")

        kwargs = {'domain_name': self.USCIS_DOMAIN}
        if self.profile_path:
            kwargs['cookie_file'] = self._resolve_cookie_file(self.profile_path)

        try:
            return fn(**kwargs)
        except Exception as e:
            print(f"Error extracting cookies from {self.browser_type}: {e}")
            raise

    def _apply_cookies(self, cookies):
        """Apply cookies to the session (from auto-login).

        Args:
            cookies: list of {name, value, domain} dicts
        """
        for c in cookies:
            domain = c.get("domain", "my.uscis.gov")
            # Ensure domain starts with a dot for broad matching
            if domain and not domain.startswith("."):
                domain = "." + domain
            self._session.cookies.set(c["name"], c["value"], domain=domain)

    def get_requests_session(self) -> requests.Session:
        """Get a requests session loaded with the latest cookies."""
        if self._cookies is None:
            try:
                self._cookies = self._extract_cookies()
            except Exception:
                # Cookie extraction failed (e.g. browser not yet opened)
                # Return empty session — auto-login will handle re-auth
                return self._session
        self._session.cookies.update(self._cookies)
        return self._session

    def refresh_from_browser(self):
        """Re-read cookies from the browser's on-disk database."""
        self._cookies = self._extract_cookies()
        self._session = requests.Session()
        self._session.cookies.update(self._cookies)

    def auto_relogin(self) -> bool:
        """Use AutoLogin to obtain a fresh session.

        Returns:
            True if re-login succeeded, False otherwise
        """
        if not self.auto_login:
            return False

        print("  Auto-login: session expired, attempting headless re-login...")
        cookie_dict = self.auto_login.login()
        if not cookie_dict:
            return False

        # Apply cookies from auto-login into the session
        self._session = requests.Session()
        self._apply_cookies(cookie_dict)
        print(f"  Auto-login: session restored with {len(cookie_dict)} cookies")
        return True
