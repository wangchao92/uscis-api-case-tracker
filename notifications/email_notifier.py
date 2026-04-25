"""Email notification service using Gmail SMTP."""

import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from datetime import datetime
from typing import Optional

from uscis.parser import CaseStatus


class EmailNotifier:
    """Sends email notifications for case status updates."""

    def __init__(
        self,
        smtp_server: str,
        smtp_port: int,
        sender_email: str,
        sender_password: str,
        recipient_email: str
    ):
        """Initialize the email notifier.

        Args:
            smtp_server: SMTP server address
            smtp_port: SMTP server port
            sender_email: Email address to send from
            sender_password: App password for sender email
            recipient_email: Email address to send notifications to
        """
        self.smtp_server = smtp_server
        self.smtp_port = smtp_port
        self.sender_email = sender_email
        self.sender_password = sender_password
        self.recipient_email = recipient_email

    def _create_html_email(
        self,
        cases: list[dict],
        changed_cases: list[str],
        json_diffs: dict[str, str] = None
    ) -> str:
        """Create HTML email content.

        Args:
            cases: List of case status dictionaries
            changed_cases: List of case numbers that have changed
            json_diffs: Dictionary mapping case numbers to diff strings

        Returns:
            HTML string for email body
        """
        json_diffs = json_diffs or {}
        html = f"""
        <!DOCTYPE html>
        <html>
        <head>
            <style>
                body {{ font-family: Arial, sans-serif; margin: 20px; }}
                .header {{ background-color: #003366; color: white; padding: 15px; }}
                .case-card {{ border: 1px solid #ddd; margin: 10px 0; padding: 15px; border-radius: 5px; }}
                .case-card.changed {{ border-color: #4CAF50; border-width: 2px; background-color: #f0fff0; }}
                .status {{ font-weight: bold; font-size: 1.2em; }}
                .changed-badge {{ background-color: #4CAF50; color: white; padding: 3px 8px; border-radius: 3px; font-size: 0.8em; }}
                .diff-badge {{ background-color: #2196F3; color: white; padding: 3px 8px; border-radius: 3px; font-size: 0.8em; }}
                .diff-section {{ background-color: #f5f5f5; padding: 10px; margin-top: 10px; border-radius: 5px; font-family: monospace; font-size: 0.85em; white-space: pre-wrap; overflow-x: auto; }}
                .diff-add {{ color: #2e7d32; background-color: #e8f5e9; }}
                .diff-del {{ color: #c62828; background-color: #ffebee; }}
                .similar-summary {{ background-color: #f5f5f5; padding: 10px; margin-top: 10px; border-radius: 5px; }}
                .progress-bar {{ background-color: #ddd; border-radius: 5px; overflow: hidden; height: 20px; }}
                .progress-fill {{ height: 100%; text-align: center; color: white; font-size: 0.8em; line-height: 20px; }}
                .approved {{ background-color: #4CAF50; }}
                .pending {{ background-color: #FFC107; }}
                .denied {{ background-color: #f44336; }}
                .footer {{ margin-top: 20px; font-size: 0.9em; color: #666; }}
                table {{ width: 100%; border-collapse: collapse; margin-top: 10px; }}
                th, td {{ padding: 8px; text-align: left; border-bottom: 1px solid #ddd; }}
                th {{ background-color: #f5f5f5; }}
            </style>
        </head>
        <body>
            <div class="header">
                <h2>USCIS Case Status Update</h2>
                <p>Checked at: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}</p>
            </div>
        """

        # Summary section
        if changed_cases:
            html += f"""
            <div style="background-color: #e8f5e9; padding: 10px; margin: 10px 0; border-radius: 5px;">
                <strong>🎉 {len(changed_cases)} case(s) have status changes!</strong>
            </div>
            """

        # Individual case cards
        for case in cases:
            is_changed = case['case_number'] in changed_cases
            has_diff = case['case_number'] in json_diffs
            card_class = "case-card changed" if (is_changed or has_diff) else "case-card"

            badges = ''
            if is_changed:
                badges += ' <span class="changed-badge">STATUS CHANGED</span>'
            if has_diff:
                badges += ' <span class="diff-badge">JSON CHANGED</span>'

            html += f"""
            <div class="{card_class}">
                <h3>
                    {case.get('case_type', '')} - {case['case_number']}
                    {badges}
                </h3>
                <p class="status">{case['status']}</p>
                <p>{case.get('title', '')}</p>
                <p><small>{case.get('description', '')[:200]}...</small></p>
            """

            # Add JSON diff if available
            if has_diff:
                diff_html = self._format_diff_html(json_diffs[case['case_number']])
                html += f"""
                <details>
                    <summary><strong>View JSON Changes</strong></summary>
                    <div class="diff-section">{diff_html}</div>
                </details>
                """

            html += "</div>"

        html += """
            <div class="footer">
                <p>This is an automated notification from USCIS Case Tracker.</p>
                <p>Next check scheduled in 1 hour.</p>
            </div>
        </body>
        </html>
        """

        return html

    def _format_diff_html(self, diff: str) -> str:
        """Format diff string as HTML with colors.

        Args:
            diff: Unified diff string

        Returns:
            HTML formatted diff
        """
        import html as html_module
        lines = []
        for line in diff.split('\n'):
            escaped = html_module.escape(line)
            if line.startswith('+') and not line.startswith('+++'):
                lines.append(f'<span class="diff-add">{escaped}</span>')
            elif line.startswith('-') and not line.startswith('---'):
                lines.append(f'<span class="diff-del">{escaped}</span>')
            else:
                lines.append(escaped)
        return '\n'.join(lines)

    def send_notification(
        self,
        cases: list[dict],
        changed_cases: list[str],
        json_diffs: Optional[dict[str, str]] = None
    ) -> bool:
        """Send a status update notification email.

        Args:
            cases: List of case status dictionaries
            changed_cases: List of case numbers that have changed
            json_diffs: Optional dictionary mapping case numbers to diff strings

        Returns:
            True if email was sent successfully, False otherwise
        """
        if not self.sender_email or not self.sender_password:
            print("Email configuration incomplete. Skipping notification.")
            return False

        json_diffs = json_diffs or {}

        try:
            msg = MIMEMultipart('alternative')

            # Subject line
            if changed_cases or json_diffs:
                parts = []
                if changed_cases:
                    parts.append(f"Status: {', '.join(changed_cases[:2])}")
                if json_diffs:
                    parts.append(f"JSON: {', '.join(list(json_diffs.keys())[:2])}")
                msg['Subject'] = f"🔔 USCIS Change: {'; '.join(parts)}"
            else:
                msg['Subject'] = f"USCIS Status Check - {datetime.now().strftime('%Y-%m-%d %H:%M')}"

            msg['From'] = self.sender_email
            msg['To'] = self.recipient_email

            # Create HTML content
            html_content = self._create_html_email(cases, changed_cases, json_diffs)
            msg.attach(MIMEText(html_content, 'html'))

            # Send email
            with smtplib.SMTP(self.smtp_server, self.smtp_port) as server:
                server.starttls()
                server.login(self.sender_email, self.sender_password)
                server.send_message(msg)

            print(f"Notification email sent to {self.recipient_email}")
            return True

        except smtplib.SMTPAuthenticationError as e:
            print(f"Email authentication failed. Make sure you're using an App Password: {e}")
            return False
        except smtplib.SMTPException as e:
            print(f"Error sending email: {e}")
            return False
        except Exception as e:
            print(f"Unexpected error sending email: {e}")
            return False

    def send_login_failure(self, reason: str) -> bool:
        """Send an email alert when auto-login fails.

        Args:
            reason: Description of what went wrong

        Returns:
            True if email was sent successfully
        """
        if not self.sender_email or not self.sender_password:
            return False

        try:
            msg = MIMEMultipart('alternative')
            msg['Subject'] = "USCIS Tracker: Login Failed - Manual Action Required"
            msg['From'] = self.sender_email
            msg['To'] = self.recipient_email

            html = f"""
            <!DOCTYPE html>
            <html><head><style>
                body {{ font-family: Arial, sans-serif; margin: 20px; }}
                .alert {{ background-color: #fff3cd; border: 2px solid #ffc107; padding: 15px; border-radius: 5px; }}
            </style></head>
            <body>
                <div class="alert">
                    <h2>USCIS Case Tracker - Login Failed</h2>
                    <p><strong>Time:</strong> {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}</p>
                    <p><strong>Reason:</strong> {reason}</p>
                    <p>Please log in manually at <a href="https://myaccount.uscis.gov/">myaccount.uscis.gov</a>
                       in the Chromium browser on your Raspberry Pi, then the tracker will resume automatically.</p>
                </div>
            </body></html>
            """
            msg.attach(MIMEText(html, 'html'))

            with smtplib.SMTP(self.smtp_server, self.smtp_port) as server:
                server.starttls()
                server.login(self.sender_email, self.sender_password)
                server.send_message(msg)

            print(f"Login failure alert sent to {self.recipient_email}")
            return True
        except Exception as e:
            print(f"Failed to send login failure alert: {e}")
            return False

    def send_test_email(self) -> bool:
        """Send a test email to verify configuration.

        Returns:
            True if test email was sent successfully
        """
        test_case = {
            'case_number': 'TEST123456789',
            'case_type': 'TEST',
            'status': 'Test Status',
            'title': 'This is a test notification',
            'description': 'If you received this email, your USCIS Case Tracker email notifications are configured correctly.'
        }

        return self.send_notification([test_case], [], {})
