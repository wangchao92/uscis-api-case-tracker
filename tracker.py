#!/usr/bin/env python3
"""USCIS Case Tracker - Main entry point.

Monitors USCIS case status changes for multiple cases with email notifications.
"""

import argparse
import sys
import time
from datetime import datetime
from pathlib import Path

import yaml
import schedule

from uscis.client import USCISClient
from uscis.cookie_manager import CookieManager
from uscis.auto_login import AutoLogin
from storage.state import StateManager
from notifications.email_notifier import EmailNotifier


class CaseTracker:
    """Main case tracker application."""

    def __init__(self, config_path: str = "config.yaml"):
        """Initialize the case tracker.

        Args:
            config_path: Path to configuration file
        """
        self.config_path = Path(config_path)
        self.config = self._load_config()
        self._init_components()

    def _load_config(self) -> dict:
        """Load configuration from YAML file."""
        if not self.config_path.exists():
            print(f"Configuration file not found: {self.config_path}")
            print("Please create a config.yaml file with your settings.")
            sys.exit(1)

        with open(self.config_path) as f:
            return yaml.safe_load(f)

    def _init_components(self):
        """Initialize all components."""
        uscis_config = self.config.get('uscis', {})
        browser_config = self.config.get('browser', {})
        browser_type = browser_config.get('type', 'chrome')
        profile_path = browser_config.get('profile_path') or None

        # Build one USCISClient per account
        self.accounts = []  # list of {name, client, cases}
        accounts_cfg = uscis_config.get('accounts', [])

        # Backwards compatibility: old single-account format
        if not accounts_cfg:
            creds = self.config.get('uscis_credentials', {})
            accounts_cfg = [{
                'name': 'Default',
                'username': creds.get('username', ''),
                'password': creds.get('password', ''),
                'verification_email': creds.get('verification_email', ''),
                'verification_app_password': creds.get('verification_app_password', ''),
                'cases': uscis_config.get('my_cases', []),
            }]

        for acct in accounts_cfg:
            name = acct.get('name', 'Account')
            username = acct.get('username', '').strip()
            password = acct.get('password', '').strip()
            # Per-account browser port and profile override global browser config
            cdp_port = acct.get('browser_port', browser_config.get('port', 9222))
            acct_profile = acct.get('browser_profile_path') or profile_path

            auto_login = AutoLogin(
                username=username,
                password=password,
                gmail_address=acct.get('verification_email', '').strip(),
                gmail_app_password=acct.get('verification_app_password', '').strip(),
                cdp_port=cdp_port
            ) if username and password else None

            cookie_manager = CookieManager(
                browser_type=browser_type,
                profile_path=acct_profile,
                auto_login=auto_login
            )
            client = USCISClient(cookie_manager=cookie_manager)
            self.accounts.append({
                'name': name,
                'client': client,
                'cases': acct.get('cases', []),
            })
            status = "auto-login enabled" if auto_login else "no credentials"
            print(f"Account '{name}': {len(acct.get('cases', []))} case(s), {status}")

        # Shared state manager and email notifier
        self.state_manager = StateManager()
        email_config = self.config.get('email', {})
        self.email_notifier = EmailNotifier(
            smtp_server=email_config.get('smtp_server', 'smtp.gmail.com'),
            smtp_port=email_config.get('smtp_port', 587),
            sender_email=email_config.get('sender_email', ''),
            sender_password=email_config.get('sender_password', ''),
            recipient_email=email_config.get('recipient_email', '')
        )

    def _check_cases_for_account(self, account: dict, all_statuses: list,
                                   changed_cases: list, json_diffs: dict) -> bool:
        """Check all cases for a single account. Returns True if any case failed."""
        uscis_client = account['client']
        login_failed = False

        for case_config in account['cases']:
            case_number = case_config.get('case_number', '')
            case_type = case_config.get('case_type', '')

            if not case_number:
                continue

            print(f"\nChecking case: {case_number} ({case_type})")

            status, raw_json = uscis_client.check_case_authenticated(
                case_number, return_raw=True
            )

            if status:
                print(f"  Status: {status.status}")
                print(f"  Title: {status.title}")

                if raw_json:
                    diff = self.state_manager.save_raw_json(case_number, raw_json)
                    if diff:
                        json_diffs[case_number] = diff
                        print(f"  *** JSON CHANGED! ***")

                is_changed = self.state_manager.update_status(status, case_type)
                if is_changed:
                    print(f"  *** STATUS CHANGED! ***")
                    changed_cases.append(case_number)

                current = self.state_manager.get_current_status(case_number)
                if current:
                    current['case_type'] = case_type
                    all_statuses.append(current)
            else:
                print(f"  Failed to retrieve status")
                login_failed = True

        return login_failed

    def check_all_cases(self, send_notification: bool = True) -> dict:
        """Check status of all configured cases across all accounts.

        Args:
            send_notification: Whether to send email notification

        Returns:
            Dictionary with check results
        """
        uscis_config = self.config.get('uscis', {})

        if not self.accounts:
            print("No accounts configured. Please add accounts to config.yaml")
            return {'error': 'No accounts configured'}

        print(f"\n{'='*60}")
        print(f"USCIS Case Status Check - {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        print(f"{'='*60}")

        all_statuses = []
        changed_cases = []
        json_diffs = {}
        any_login_failed = False

        for account in self.accounts:
            if len(self.accounts) > 1:
                print(f"\n--- Account: {account['name']} ---")
            failed = self._check_cases_for_account(
                account, all_statuses, changed_cases, json_diffs
            )
            if failed:
                any_login_failed = True

        # Send login failure alert if no cases at all could be checked
        if any_login_failed and not all_statuses and send_notification:
            self.email_notifier.send_login_failure(
                "Could not check any cases. Session expired and auto-login failed. "
                "This may be due to: account lockout, expired credentials, "
                "or verification code not received."
            )

        # Print JSON diffs if any
        if json_diffs:
            print(f"\n{'='*60}")
            print("JSON DIFFERENCES DETECTED:")
            print(f"{'='*60}")
            for case_num, diff in json_diffs.items():
                print(f"\n--- {case_num} ---")
                print(diff)

        # Send notification if configured
        if send_notification and all_statuses:
            if changed_cases or json_diffs:
                print(f"\n{len(changed_cases)} status change(s), {len(json_diffs)} JSON change(s), sending notification...")
                self.email_notifier.send_notification(
                    all_statuses, changed_cases, json_diffs
                )
            else:
                print("\nNo changes detected.")

        print(f"\n{'='*60}")
        print(f"Check complete. Next check in {uscis_config.get('check_interval_hours', 4)} hours.")
        print(f"{'='*60}\n")

        return {
            'cases_checked': len(all_statuses),
            'changes_detected': len(changed_cases),
            'changed_cases': changed_cases,
            'json_diffs': json_diffs,
        }

    def run_once(self):
        """Run a single check and exit."""
        self.check_all_cases()

    def run_continuous(self):
        """Run continuous monitoring with scheduled checks."""
        uscis_config = self.config.get('uscis', {})
        interval_hours = uscis_config.get('check_interval_hours', 1)

        print(f"Starting USCIS Case Tracker")
        print(f"Check interval: every {interval_hours} hours")
        print(f"Press Ctrl+C to stop\n")

        # Run initial check
        self.check_all_cases()

        # Schedule periodic checks
        schedule.every(interval_hours).hours.do(self.check_all_cases)

        # Run scheduler
        try:
            while True:
                schedule.run_pending()
                time.sleep(60)  # Check every minute
        except KeyboardInterrupt:
            print("\nStopping USCIS Case Tracker...")

    def test_email(self):
        """Send a test email to verify configuration."""
        print("Sending test email...")
        if self.email_notifier.send_test_email():
            print("Test email sent successfully!")
        else:
            print("Failed to send test email. Check your email configuration.")

    def show_history(self, case_number: str):
        """Show status history for a case.

        Args:
            case_number: Case number to show history for
        """
        history = self.state_manager.get_case_history(case_number)

        if not history:
            print(f"No history found for case {case_number}")
            return

        print(f"\nStatus history for {case_number}:")
        print("-" * 60)
        for record in history:
            print(f"  {record['checked_at']}: {record['status']}")
        print()

    def show_status(self):
        """Show current status of all tracked cases."""
        statuses = self.state_manager.get_all_current_statuses()

        if not statuses:
            print("No cases being tracked yet.")
            return

        print(f"\nCurrent status of tracked cases:")
        print("=" * 60)
        for status in statuses:
            print(f"\n{status['case_type']} - {status['case_number']}")
            print(f"  Status: {status['status']}")
            print(f"  Last checked: {status['last_checked']}")
            print(f"  Last changed: {status['last_changed']}")
        print()

    def show_raw_json(self, case_number: str):
        """Show current raw JSON for a case.

        Args:
            case_number: Case number to show raw JSON for
        """
        raw = self.state_manager.get_current_raw_json(case_number)

        if not raw:
            print(f"No raw JSON stored for case {case_number}")
            return

        print(f"\nRaw JSON for {case_number}:")
        print("=" * 60)
        print(raw)
        print()


def main():
    """Main entry point."""
    parser = argparse.ArgumentParser(
        description="USCIS Case Status Tracker",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python tracker.py                  # Start continuous monitoring
  python tracker.py --once           # Run single check and exit
  python tracker.py --test-email     # Send test email
  python tracker.py --status         # Show current status of all cases
  python tracker.py --history IOE123 # Show history for a case
  python tracker.py --raw IOE123     # Show raw JSON for a case
        """
    )

    parser.add_argument(
        '--config', '-c',
        default='config.yaml',
        help='Path to configuration file (default: config.yaml)'
    )
    parser.add_argument(
        '--once',
        action='store_true',
        help='Run a single check and exit'
    )
    parser.add_argument(
        '--test-email',
        action='store_true',
        help='Send a test email to verify configuration'
    )
    parser.add_argument(
        '--status',
        action='store_true',
        help='Show current status of all tracked cases'
    )
    parser.add_argument(
        '--history',
        metavar='CASE_NUMBER',
        help='Show status history for a specific case'
    )
    parser.add_argument(
        '--raw',
        metavar='CASE_NUMBER',
        help='Show raw JSON response for a specific case'
    )
    parser.add_argument(
        '--no-notify',
        action='store_true',
        help='Skip sending email notifications'
    )

    args = parser.parse_args()

    # Initialize tracker
    tracker = CaseTracker(config_path=args.config)

    # Handle different modes
    if args.test_email:
        tracker.test_email()
    elif args.status:
        tracker.show_status()
    elif args.history:
        tracker.show_history(args.history)
    elif args.raw:
        tracker.show_raw_json(args.raw)
    elif args.once:
        tracker.check_all_cases(send_notification=not args.no_notify)
    else:
        tracker.run_continuous()


if __name__ == '__main__':
    main()
