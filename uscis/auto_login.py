"""Automated USCIS session refresh using the running Chromium browser."""

import time
import imaplib
import email as email_lib
import email.utils
import re
import shutil
from pathlib import Path
import requests
from typing import Optional

from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.chromium.options import ChromiumOptions
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import TimeoutException, WebDriverException, NoSuchElementException

USCIS_ACCOUNT_URL = "https://myaccount.uscis.gov/"
USCIS_API_DOMAIN = "my.uscis.gov"


class AutoLogin:
    """Refreshes USCIS session by controlling the user's running Chromium."""

    def __init__(
        self,
        username: str,
        password: str,
        gmail_address: str = "",
        gmail_app_password: str = "",
        cdp_port: int = 9222
    ):
        self.username = username
        self.password = password
        self.gmail_address = gmail_address
        self.gmail_app_password = gmail_app_password
        self.cdp_port = cdp_port

    def _cdp_available(self) -> bool:
        try:
            resp = requests.get(f"http://127.0.0.1:{self.cdp_port}/json", timeout=2)
            return resp.status_code == 200
        except Exception:
            return False

    def _connect_to_running_chromium(self) -> webdriver.Chrome:
        options = ChromiumOptions()
        options.add_experimental_option("debuggerAddress", f"127.0.0.1:{self.cdp_port}")
        # Prefer a locally-installed chromedriver (needed on ARM Linux where
        # Selenium Manager can't auto-download a binary). Fall back to Selenium
        # Manager on platforms where it's supported.
        driver_path = self._find_chromedriver()
        service = Service(executable_path=driver_path) if driver_path else Service()
        return webdriver.Chrome(service=service, options=options)

    @staticmethod
    def _find_chromedriver() -> Optional[str]:
        """Locate a chromedriver binary on the system, or return None."""
        for candidate in ("chromedriver", "chromedriver.exe"):
            found = shutil.which(candidate)
            if found:
                return found
        for path in ("/usr/bin/chromedriver", "/usr/local/bin/chromedriver"):
            if Path(path).exists():
                return path
        return None

    def login(self) -> Optional[list]:
        """Refresh the USCIS session and return fresh cookies as list of {name, value, domain}."""
        if self._cdp_available():
            print("  Auto-login: connecting to running Chromium...")
            return self._refresh_via_cdp()
        else:
            print("  Auto-login: Chromium remote debugging not available")
            print(f"  Tip: run: python start_browser.py")
            return None

    def _refresh_via_cdp(self) -> Optional[list]:
        """Open a tab in the running Chromium to refresh the USCIS session."""
        driver = None
        original_handle = None
        new_handle = None
        try:
            driver = self._connect_to_running_chromium()
            original_handle = driver.current_window_handle

            driver.switch_to.new_window('tab')
            new_handle = driver.current_window_handle
            driver.get(USCIS_ACCOUNT_URL)
            time.sleep(5)

            # Navigate through whatever pages are needed until we're logged in
            result = self._navigate_to_logged_in(driver)

            if result is not None:
                # Also get cookies from the API domain
                driver.get(f"https://{USCIS_API_DOMAIN}/account")
                time.sleep(3)
                api_cookies = self._extract_all_cookies(driver)
                # Merge, avoiding duplicates by (name, domain)
                seen = {(c["name"], c["domain"]) for c in result}
                for c in api_cookies:
                    if (c["name"], c["domain"]) not in seen:
                        result.append(c)

            return result

        except WebDriverException as e:
            print(f"  Auto-login: CDP error: {e}")
            return None
        finally:
            if driver and new_handle:
                try:
                    driver.close()
                    if original_handle:
                        driver.switch_to.window(original_handle)
                except Exception:
                    pass

    def _navigate_to_logged_in(self, driver: webdriver.Chrome) -> Optional[list]:
        """Handle whatever page we land on until we're fully logged in."""
        wait = WebDriverWait(driver, 30)
        verification_attempted = False  # Only try verification code ONCE to avoid lockout

        for attempt in range(5):
            current_url = driver.current_url
            print(f"  Auto-login: at {current_url[:70]}")

            # Case 1: On the sign-in page — need to enter credentials
            if "sign-in" in current_url:
                print("  Auto-login: on sign-in page, logging in...")
                if not self._do_sign_in(driver, wait):
                    return None
                time.sleep(5)
                continue  # Re-check where we ended up

            # Case 2: On the /auth page — need to enter verification code
            if "/auth" in current_url:
                if verification_attempted:
                    print("  Auto-login: still on /auth after submitting code — stopping to avoid lockout")
                    return None
                print("  Auto-login: on verification code page...")
                verification_attempted = True
                if not self._do_verification_code(driver, wait):
                    return None
                time.sleep(5)
                continue  # Re-check where we ended up

            # Case 3: On the login page (old style)
            if "login" in current_url and "sign-in" not in current_url:
                print("  Auto-login: on login page, signing in...")
                if not self._do_sign_in(driver, wait):
                    return None
                time.sleep(5)
                continue

            # Case 4: We're past auth — logged in!
            print("  Auto-login: logged in successfully!")
            return self._extract_all_cookies(driver)

        print("  Auto-login: could not complete login after multiple steps")
        return None

    def _do_sign_in(self, driver: webdriver.Chrome, wait: WebDriverWait) -> bool:
        """Handle the sign-in page at myaccount.uscis.gov/sign-in."""
        try:
            # Try clicking Sign In first (browser auto-fill handles credentials)
            sign_in_btn = wait.until(
                EC.element_to_be_clickable((By.ID, "sign-in-btn"))
            )
            sign_in_btn.click()
            print("  Auto-login: clicked Sign In (auto-fill)...")
            time.sleep(5)

            # If still on sign-in page, fill manually
            if "sign-in" in driver.current_url:
                print("  Auto-login: auto-fill didn't work, filling manually...")
                email_input = driver.find_element(By.ID, "email-address")
                password_input = driver.find_element(By.ID, "password")
                email_input.clear()
                email_input.send_keys(self.username)
                password_input.clear()
                password_input.send_keys(self.password)
                driver.find_element(By.ID, "sign-in-btn").click()
                time.sleep(5)

            return True
        except (TimeoutException, NoSuchElementException) as e:
            print(f"  Auto-login: sign-in failed: {e}")
            return False

    def _do_verification_code(self, driver: webdriver.Chrome, wait: WebDriverWait) -> bool:
        """Handle the verification code page at myaccount.uscis.gov/auth."""
        try:
            # Record the timestamp of the latest USCIS email so we can detect the new one
            latest_before = self._get_latest_uscis_email_time()

            # USCIS auto-sends a code after login — wait for it first (up to 60s)
            print("  Auto-login: waiting for verification code email...")
            code = self._fetch_verification_code(max_wait=60, sent_after=latest_before)

            # If no code arrived within 60s, click resend and wait again
            if not code:
                print("  Auto-login: no code received, clicking resend...")
                try:
                    resend = driver.find_element(By.XPATH,
                        "//button[contains(text(), 'request a new verification code')]")
                    resend.click()
                    print("  Auto-login: clicked 'request a new verification code'")
                except NoSuchElementException:
                    print("  Auto-login: no resend button found")
                    return False
                code = self._fetch_verification_code(max_wait=60, sent_after=latest_before)
            if not code:
                print("  Auto-login: could not retrieve verification code")
                return False

            print(f"  Auto-login: got code: {code}")

            # Enter the code using the exact field ID from the page
            code_input = wait.until(
                EC.presence_of_element_located((By.ID, "secure-verification-code"))
            )
            code_input.clear()
            code_input.send_keys(code)

            # Check "Remember this browser" if available
            try:
                remember = driver.find_element(By.CSS_SELECTOR,
                    "input[type='checkbox']")
                if not remember.is_selected():
                    remember.click()
                    print("  Auto-login: checked 'Remember this browser'")
            except NoSuchElementException:
                pass

            # Click Submit
            submit_btn = driver.find_element(By.ID, "2fa-submit-btn")
            submit_btn.click()
            print("  Auto-login: submitted verification code")
            time.sleep(5)

            return True
        except (TimeoutException, NoSuchElementException) as e:
            print(f"  Auto-login: verification code entry failed: {e}")
            return False

    def _extract_all_cookies(self, driver: webdriver.Chrome) -> list[dict]:
        """Extract cookies preserving domain info."""
        return [{"name": c["name"], "value": c["value"], "domain": c.get("domain", "")}
                for c in driver.get_cookies()]

    def _get_latest_uscis_email_time(self) -> float:
        """Get the timestamp of the most recent USCIS email (read or unread).

        Returns unix timestamp, or 0 if no emails found.
        """
        try:
            mail = imaplib.IMAP4_SSL("imap.gmail.com")
            mail.login(self.gmail_address, self.gmail_app_password)
            mail.select("inbox")
            _, msg_ids = mail.search(None, '(FROM "uscis")')
            ids = msg_ids[0].split()
            if ids:
                # Fetch date of the newest email
                _, msg_data = mail.fetch(ids[-1], "(RFC822)")
                raw = msg_data[0][1]
                msg = email_lib.message_from_bytes(raw)
                date_str = msg.get("Date", "")
                if date_str:
                    dt = email.utils.parsedate_to_datetime(date_str)
                    mail.logout()
                    ts = dt.timestamp()
                    print(f"  Auto-login: latest USCIS email is from {date_str}")
                    return ts
            mail.logout()
        except Exception as e:
            print(f"  Auto-login: could not check latest email time: {e}")
        return 0

    def _fetch_verification_code(self, max_wait: int = 90, sent_after: float = 0) -> Optional[str]:
        """Fetch verification code from Gmail via IMAP.

        Searches ALL USCIS emails (not just unread) and filters by date,
        since the user's phone/watch may auto-mark emails as read.

        Args:
            max_wait: Maximum seconds to wait for the email
            sent_after: Unix timestamp — only consider emails sent after this time
        """
        if not self.gmail_address or not self.gmail_app_password:
            print("  Auto-login: Gmail IMAP not configured — cannot fetch verification code")
            print("  Add verification_email and verification_app_password to config.yaml")
            return None

        print(f"  Auto-login: waiting for fresh USCIS verification email (up to {max_wait}s)...")
        deadline = time.time() + max_wait
        # Wait a few seconds for the email to arrive
        time.sleep(10)
        attempt = 0

        while time.time() < deadline:
            attempt += 1
            try:
                mail = imaplib.IMAP4_SSL("imap.gmail.com")
                mail.login(self.gmail_address, self.gmail_app_password)
                mail.select("inbox")

                # Search ALL USCIS emails (read or unread) — phone may auto-read them
                _, msg_ids = mail.search(None, '(FROM "uscis")')
                ids = msg_ids[0].split()

                if attempt == 1 or attempt % 4 == 0:
                    print(f"  Auto-login: poll #{attempt}, checking last few of {len(ids)} USCIS emails")

                # Only check the last 5 emails (newest are at the end)
                for msg_id in reversed(ids[-5:]):
                    _, msg_data = mail.fetch(msg_id, "(RFC822)")
                    raw = msg_data[0][1]
                    msg = email_lib.message_from_bytes(raw)

                    # Check email date — skip if older than sent_after
                    date_str = msg.get("Date", "")
                    if date_str and sent_after:
                        try:
                            email_time = email.utils.parsedate_to_datetime(date_str)
                            if email_time.timestamp() < sent_after:
                                continue  # This email is from before we requested the code
                        except Exception:
                            pass

                    # Extract body
                    body = ""
                    if msg.is_multipart():
                        for part in msg.walk():
                            ct = part.get_content_type()
                            if ct == "text/plain" or ct == "text/html":
                                payload = part.get_payload(decode=True)
                                if payload:
                                    body += payload.decode(errors="ignore")
                    else:
                        payload = msg.get_payload(decode=True)
                        if payload:
                            body = payload.decode(errors="ignore")

                    # Look for 6-digit verification code in the USCIS email HTML
                    # The code appears in a styled span like: <span style='...'>265962</span>
                    # or after text like "verification code:"
                    code_match = (
                        # Match code inside HTML span/tag with styling (USCIS format)
                        re.search(r"font-weight:\s*600;?'?>(\d{6})<", body)
                        # Match "verification code: 123456" in plain text
                        or re.search(r'verification code[:\s]+(\d{6})', body, re.I)
                        # Match "enter this.*code: 123456"
                        or re.search(r'enter this[^<]{0,50}code[:\s]+(\d{6})', body, re.I)
                    )

                    if code_match:
                        code = code_match.group(1)
                        mail.logout()
                        return code

                mail.logout()
            except imaplib.IMAP4.error as e:
                print(f"  Auto-login: IMAP auth error: {e}")
                print("  Check verification_email and verification_app_password in config.yaml")
                return None  # Don't retry auth errors
            except Exception as e:
                print(f"  Auto-login: IMAP error: {e}")

            time.sleep(5)

        print("  Auto-login: timed out waiting for verification code email")
        return None
