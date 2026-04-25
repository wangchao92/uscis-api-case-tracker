"""USCIS API client for checking case status."""

import time
import re
from typing import Optional, Tuple, Union

import requests

from .cookie_manager import CookieManager
from .parser import CaseStatus, parse_case_status, parse_authenticated_case_status


class USCISClient:
    """Client for interacting with USCIS case status APIs."""

    PUBLIC_API_URL = "https://egov.uscis.gov/csol-api/case-statuses/{case_number}"
    PRIVATE_API_URL = "https://my.uscis.gov/account/case-service/api/cases/{case_number}"

    DEFAULT_HEADERS = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
        'Accept': 'application/json',
        'Accept-Language': 'en-US,en;q=0.9',
    }

    def __init__(self, cookie_manager: Optional[CookieManager] = None):
        """Initialize the USCIS client.

        Args:
            cookie_manager: Optional CookieManager for authenticated requests
        """
        self.cookie_manager = cookie_manager
        self._public_session = requests.Session()
        self._public_session.headers.update(self.DEFAULT_HEADERS)

    def check_case_public(self, case_number: str) -> Optional[CaseStatus]:
        """Check case status using the public API (no authentication required).

        Args:
            case_number: USCIS case number (e.g., 'IOE0934045988')

        Returns:
            CaseStatus object or None if request failed
        """
        url = self.PUBLIC_API_URL.format(case_number=case_number)

        try:
            response = self._public_session.get(url, timeout=30)
            response.raise_for_status()
            data = response.json()
            return parse_case_status(data, case_number)
        except requests.RequestException as e:
            print(f"Error checking case {case_number} (public API): {e}")
            return None
        except ValueError as e:
            print(f"Error parsing response for {case_number}: {e}")
            return None

    def check_case_authenticated(
        self,
        case_number: str,
        return_raw: bool = False
    ) -> Union[Tuple[Optional[CaseStatus], Optional[dict]], Optional[CaseStatus]]:
        """Check case status using the authenticated API (requires login cookies).

        Args:
            case_number: USCIS case number (e.g., 'IOE0934045988')
            return_raw: If True, return tuple of (CaseStatus, raw_json)

        Returns:
            CaseStatus object (or tuple with raw JSON if return_raw=True)
        """
        if not self.cookie_manager:
            print("No cookie manager configured, using public API")
            result = self.check_case_public(case_number)
            return (result, None) if return_raw else result

        # Try authenticated API directly - fall back to public if it fails
        url = self.PRIVATE_API_URL.format(case_number=case_number)
        try:
            session = self.cookie_manager.get_requests_session()
            session.headers.update(self.DEFAULT_HEADERS)
        except Exception as e:
            print(f"Could not get cookies: {e}, using public API")
            result = self.check_case_public(case_number)
            return (result, None) if return_raw else result

        try:
            response = session.get(url, timeout=30)

            # On 401/403/500, try to recover the session and retry once
            # 500 can mean the case belongs to a different account (wrong session cookies)
            if response.status_code in (401, 403, 500):
                recovered = False

                # 1. Try re-login via headless Chromium (if credentials configured)
                if self.cookie_manager.auto_relogin():
                    recovered = True
                else:
                    # 2. Fall back to re-reading cookies from browser on-disk db
                    print(f"  Session expired (status {response.status_code}), refreshing from browser...")
                    self.cookie_manager.refresh_from_browser()
                    recovered = True  # attempt regardless, API call will tell us

                if recovered:
                    session = self.cookie_manager.get_requests_session()
                    session.headers.update(self.DEFAULT_HEADERS)
                    response = session.get(url, timeout=30)

            # If still failing, fall back to public API
            if response.status_code in (301, 302, 303, 307, 308, 401, 403, 500):
                print(f"  Still failing after session recovery (status {response.status_code}), using public API")
                if not self.cookie_manager.auto_login:
                    print("  Tip: add uscis_credentials to config.yaml for automatic re-login")
                result = self.check_case_public(case_number)
                return (result, None) if return_raw else result

            response.raise_for_status()
            data = response.json()
            parsed = parse_authenticated_case_status(data, case_number)
            return (parsed, data) if return_raw else parsed
        except requests.RequestException as e:
            print(f"Error checking case {case_number} (authenticated): {e}")
            result = self.check_case_public(case_number)
            return (result, None) if return_raw else result
        except ValueError as e:
            print(f"Error parsing authenticated response for {case_number}: {e}")
            result = self.check_case_public(case_number)
            return (result, None) if return_raw else result

    def check_similar_cases(
        self,
        base_case_number: str,
        range_size: int = 50,
        delay_between_requests: float = 0.5
    ) -> list[CaseStatus]:
        """Check similar cases around a given case number.

        Args:
            base_case_number: Base case number to check around
            range_size: Number of cases to check on each side (±range_size)
            delay_between_requests: Delay in seconds between requests to avoid rate limiting

        Returns:
            List of CaseStatus objects for successfully checked cases
        """
        # Extract prefix and number from case number
        # Format is typically: IOE0934045988 (3 letter prefix + numbers)
        match = re.match(r'^([A-Z]+)(\d+)$', base_case_number.upper())
        if not match:
            print(f"Invalid case number format: {base_case_number}")
            return []

        prefix = match.group(1)
        base_number = int(match.group(2))
        number_length = len(match.group(2))

        results = []

        # Generate case numbers in range
        start = max(0, base_number - range_size)
        end = base_number + range_size + 1

        print(f"Checking {end - start} similar cases around {base_case_number}...")

        for num in range(start, end):
            case_number = f"{prefix}{str(num).zfill(number_length)}"

            # Skip the base case itself
            if case_number == base_case_number.upper():
                continue

            status = self.check_case_public(case_number)
            if status:
                results.append(status)

            # Respect rate limits
            if delay_between_requests > 0:
                time.sleep(delay_between_requests)

        print(f"Successfully checked {len(results)} similar cases")
        return results
