"""SQLite storage for case history and state management."""

import sqlite3
import json
import difflib
from datetime import datetime
from pathlib import Path
from typing import Optional
from contextlib import contextmanager

from uscis.parser import CaseStatus, SimilarCasesSummary


class StateManager:
    """Manages SQLite storage for case status history."""

    def __init__(self, db_path: str = "data/cases.db"):
        """Initialize the state manager.

        Args:
            db_path: Path to SQLite database file
        """
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_database()

    @contextmanager
    def _get_connection(self):
        """Context manager for database connections."""
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()

    def _init_database(self):
        """Initialize database tables."""
        with self._get_connection() as conn:
            cursor = conn.cursor()

            # Case status history table
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS case_history (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    case_number TEXT NOT NULL,
                    case_type TEXT,
                    status TEXT NOT NULL,
                    title TEXT,
                    description TEXT,
                    form_type TEXT,
                    received_date TEXT,
                    checked_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    UNIQUE(case_number, status, checked_at)
                )
            ''')

            # Current case status table (latest known status per case)
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS current_status (
                    case_number TEXT PRIMARY KEY,
                    case_type TEXT,
                    status TEXT NOT NULL,
                    title TEXT,
                    description TEXT,
                    form_type TEXT,
                    received_date TEXT,
                    last_checked TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    last_changed TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            ''')

            # Similar cases summary table
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS similar_cases_summary (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    base_case_number TEXT NOT NULL,
                    total_checked INTEGER,
                    approved_count INTEGER,
                    pending_count INTEGER,
                    denied_count INTEGER,
                    status_counts TEXT,  -- JSON string
                    checked_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            ''')

            # Raw JSON responses table for character-level diff
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS raw_responses (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    case_number TEXT NOT NULL,
                    raw_json TEXT NOT NULL,
                    checked_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            ''')

            # Current raw JSON (latest per case)
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS current_raw (
                    case_number TEXT PRIMARY KEY,
                    raw_json TEXT NOT NULL,
                    last_checked TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            ''')

            # Create indexes for faster queries
            cursor.execute('''
                CREATE INDEX IF NOT EXISTS idx_case_history_case_number
                ON case_history(case_number)
            ''')
            cursor.execute('''
                CREATE INDEX IF NOT EXISTS idx_case_history_checked_at
                ON case_history(checked_at)
            ''')
            cursor.execute('''
                CREATE INDEX IF NOT EXISTS idx_raw_responses_case_number
                ON raw_responses(case_number)
            ''')

    def get_current_status(self, case_number: str) -> Optional[dict]:
        """Get the current stored status for a case.

        Args:
            case_number: USCIS case number

        Returns:
            Dictionary with current status info or None if not found
        """
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                'SELECT * FROM current_status WHERE case_number = ?',
                (case_number,)
            )
            row = cursor.fetchone()
            return dict(row) if row else None

    def update_status(self, case_status: CaseStatus, case_type: str = "") -> bool:
        """Update the status for a case and record history.

        Args:
            case_status: CaseStatus object with current status
            case_type: Type of case (e.g., 'I-485', 'I-765')

        Returns:
            True if status changed, False otherwise
        """
        current = self.get_current_status(case_status.case_number)
        status_changed = False

        with self._get_connection() as conn:
            cursor = conn.cursor()
            now = datetime.now().isoformat()

            if current is None:
                # New case, insert
                cursor.execute('''
                    INSERT INTO current_status
                    (case_number, case_type, status, title, description,
                     form_type, received_date, last_checked, last_changed)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ''', (
                    case_status.case_number,
                    case_type or case_status.form_type,
                    case_status.status,
                    case_status.title,
                    case_status.description,
                    case_status.form_type,
                    case_status.received_date,
                    now,
                    now
                ))
                status_changed = True
            else:
                # Check if status changed
                if current['status'] != case_status.status:
                    status_changed = True
                    cursor.execute('''
                        UPDATE current_status
                        SET status = ?, title = ?, description = ?,
                            form_type = ?, received_date = ?,
                            last_checked = ?, last_changed = ?
                        WHERE case_number = ?
                    ''', (
                        case_status.status,
                        case_status.title,
                        case_status.description,
                        case_status.form_type,
                        case_status.received_date,
                        now,
                        now,
                        case_status.case_number
                    ))
                else:
                    # Status unchanged — update last_checked and refresh description/title
                    cursor.execute('''
                        UPDATE current_status
                        SET last_checked = ?, description = ?, title = ?
                        WHERE case_number = ?
                    ''', (now, case_status.description, case_status.title,
                          case_status.case_number))

            # Record in history
            cursor.execute('''
                INSERT INTO case_history
                (case_number, case_type, status, title, description,
                 form_type, received_date, checked_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ''', (
                case_status.case_number,
                case_type or case_status.form_type,
                case_status.status,
                case_status.title,
                case_status.description,
                case_status.form_type,
                case_status.received_date,
                now
            ))

        return status_changed

    def save_similar_cases_summary(self, summary: SimilarCasesSummary):
        """Save a similar cases summary.

        Args:
            summary: SimilarCasesSummary object
        """
        import json

        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('''
                INSERT INTO similar_cases_summary
                (base_case_number, total_checked, approved_count,
                 pending_count, denied_count, status_counts)
                VALUES (?, ?, ?, ?, ?, ?)
            ''', (
                summary.base_case_number,
                summary.total_checked,
                summary.approved_count,
                summary.pending_count,
                summary.denied_count,
                json.dumps(summary.status_counts)
            ))

    def get_case_history(
        self,
        case_number: str,
        limit: int = 100
    ) -> list[dict]:
        """Get status history for a case.

        Args:
            case_number: USCIS case number
            limit: Maximum number of records to return

        Returns:
            List of history records
        """
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('''
                SELECT * FROM case_history
                WHERE case_number = ?
                ORDER BY checked_at DESC
                LIMIT ?
            ''', (case_number, limit))
            return [dict(row) for row in cursor.fetchall()]

    def get_latest_similar_summary(
        self,
        base_case_number: str
    ) -> Optional[dict]:
        """Get the latest similar cases summary for a case.

        Args:
            base_case_number: Base case number

        Returns:
            Dictionary with summary or None
        """
        import json

        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('''
                SELECT * FROM similar_cases_summary
                WHERE base_case_number = ?
                ORDER BY checked_at DESC
                LIMIT 1
            ''', (base_case_number,))
            row = cursor.fetchone()
            if row:
                result = dict(row)
                result['status_counts'] = json.loads(result['status_counts'])
                return result
            return None

    def get_all_current_statuses(self) -> list[dict]:
        """Get current status for all tracked cases.

        Returns:
            List of current status records
        """
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('SELECT * FROM current_status ORDER BY case_number')
            return [dict(row) for row in cursor.fetchall()]

    def save_raw_json(self, case_number: str, raw_json: dict) -> Optional[str]:
        """Save raw JSON response and return diff if changed.

        Args:
            case_number: USCIS case number
            raw_json: Raw JSON response from API

        Returns:
            Diff string if JSON changed, None if no change or first save
        """
        # Pretty-print JSON for readable diffs
        new_json_str = json.dumps(raw_json, indent=2, sort_keys=True)
        diff_result = None

        with self._get_connection() as conn:
            cursor = conn.cursor()
            now = datetime.now().isoformat()

            # Get current raw JSON
            cursor.execute(
                'SELECT raw_json FROM current_raw WHERE case_number = ?',
                (case_number,)
            )
            row = cursor.fetchone()

            if row:
                old_json_str = row['raw_json']
                if old_json_str != new_json_str:
                    # Generate character-level diff
                    diff_result = self._generate_diff(old_json_str, new_json_str)

                    # Update current
                    cursor.execute('''
                        UPDATE current_raw
                        SET raw_json = ?, last_checked = ?
                        WHERE case_number = ?
                    ''', (new_json_str, now, case_number))
                else:
                    # Just update timestamp
                    cursor.execute('''
                        UPDATE current_raw
                        SET last_checked = ?
                        WHERE case_number = ?
                    ''', (now, case_number))
            else:
                # First time, insert
                cursor.execute('''
                    INSERT INTO current_raw (case_number, raw_json, last_checked)
                    VALUES (?, ?, ?)
                ''', (case_number, new_json_str, now))

            # Always save to history
            cursor.execute('''
                INSERT INTO raw_responses (case_number, raw_json, checked_at)
                VALUES (?, ?, ?)
            ''', (case_number, new_json_str, now))

        return diff_result

    def _generate_diff(self, old_str: str, new_str: str) -> str:
        """Generate a character-level diff between two strings.

        Args:
            old_str: Previous JSON string
            new_str: New JSON string

        Returns:
            Formatted diff string
        """
        diff_lines = list(difflib.unified_diff(
            old_str.splitlines(keepends=True),
            new_str.splitlines(keepends=True),
            fromfile='previous',
            tofile='current',
            lineterm=''
        ))

        if not diff_lines:
            return ""

        return ''.join(diff_lines)

    def get_raw_json_history(
        self,
        case_number: str,
        limit: int = 10
    ) -> list[dict]:
        """Get raw JSON history for a case.

        Args:
            case_number: USCIS case number
            limit: Maximum number of records

        Returns:
            List of raw JSON records
        """
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('''
                SELECT * FROM raw_responses
                WHERE case_number = ?
                ORDER BY checked_at DESC
                LIMIT ?
            ''', (case_number, limit))
            return [dict(row) for row in cursor.fetchall()]

    def get_current_raw_json(self, case_number: str) -> Optional[str]:
        """Get current raw JSON for a case.

        Args:
            case_number: USCIS case number

        Returns:
            Raw JSON string or None
        """
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                'SELECT raw_json FROM current_raw WHERE case_number = ?',
                (case_number,)
            )
            row = cursor.fetchone()
            return row['raw_json'] if row else None
