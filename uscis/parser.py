"""Response parsing utilities for USCIS API responses."""

from typing import Optional
from dataclasses import dataclass
from datetime import datetime


@dataclass
class CaseStatus:
    """Represents a parsed case status."""
    case_number: str
    status: str
    title: str
    description: str
    form_type: Optional[str] = None
    received_date: Optional[str] = None
    last_updated: Optional[datetime] = None

    def __str__(self) -> str:
        return f"{self.case_number}: {self.status} - {self.title}"


@dataclass
class SimilarCasesSummary:
    """Summary of similar cases analysis."""
    base_case_number: str
    total_checked: int
    status_counts: dict  # status -> count
    approved_count: int
    pending_count: int
    denied_count: int

    @property
    def approval_rate(self) -> float:
        if self.total_checked == 0:
            return 0.0
        return (self.approved_count / self.total_checked) * 100


def parse_case_status(response_data: dict, case_number: str) -> Optional[CaseStatus]:
    """Parse case status from public API response.

    Public API endpoint: https://egov.uscis.gov/csol-api/case-statuses/{case_number}
    """
    if not response_data:
        return None

    try:
        case_status = response_data.get('CaseStatusResponse', {})

        status = case_status.get('detailsEng', {}).get('actionCodeText', 'Unknown')
        title = case_status.get('detailsEng', {}).get('actionCodeDesc', '')
        description = case_status.get('detailsEng', {}).get('actionCodeDescLong', '')
        form_type = case_status.get('formNum', '')
        received_date = case_status.get('receiptDate', '')

        return CaseStatus(
            case_number=case_number,
            status=status,
            title=title,
            description=description,
            form_type=form_type,
            received_date=received_date,
            last_updated=datetime.now()
        )
    except (KeyError, TypeError) as e:
        print(f"Error parsing case status for {case_number}: {e}")
        return None


def parse_authenticated_case_status(response_data: dict, case_number: str) -> Optional[CaseStatus]:
    """Parse case status from authenticated API response.

    Private API endpoint: https://my.uscis.gov/account/case-service/api/cases/{case_number}
    """
    if not response_data:
        return None

    # Event code to human-readable status mapping
    EVENT_CODE_MAP = {
        'IAF': 'Case Was Received',
        'FTA0': 'Fingerprints Were Taken',
        'FTA1': 'Fingerprints Were Taken',
        'RFE': 'Request for Evidence Was Sent',
        'RFEC': 'Response To RFE Was Received',
        'INT': 'Interview Was Scheduled',
        'INTC': 'Interview Was Completed',
        'APR': 'Case Was Approved',
        'DEN': 'Case Was Denied',
        'CPR': 'Card Is Being Produced',
        'CPM': 'Card Was Mailed',
        'CPP': 'Card Was Picked Up',
        'WDN': 'Case Was Withdrawn',
        'TRN': 'Case Was Transferred',
        'ADJ': 'Case Is Being Actively Reviewed',
    }

    try:
        data = response_data.get('data', response_data)

        form_type = data.get('formType', '')
        form_name = data.get('formName', '')
        received_date = data.get('submissionDate', '')
        updated_at = data.get('updatedAt', '')
        applicant_name = data.get('applicantName', '')

        # Get status from most recent event
        events = data.get('events', [])
        status = 'Case Received'
        latest_event_date = ''

        if events:
            # Sort by timestamp to get the most recent
            sorted_events = sorted(
                events,
                key=lambda e: e.get('createdAtTimestamp', e.get('createdAt', '')),
                reverse=True
            )
            if sorted_events:
                latest_event = sorted_events[0]
                event_code = latest_event.get('eventCode', '')
                status = EVENT_CODE_MAP.get(event_code, f'Event: {event_code}')
                latest_event_date = latest_event.get('createdAt', '')

        # Check for notices (appointments, etc.)
        notices = data.get('notices', [])
        notice_info = ''
        if notices:
            latest_notice = notices[0]
            action_type = latest_notice.get('actionType', '')
            if action_type:
                notice_info = f" ({action_type})"

        # Build description
        description = f"Form: {form_name}"
        if applicant_name:
            description += f"\nApplicant: {applicant_name}"
        if updated_at:
            description += f"\nLast Updated: {updated_at}"
        if latest_event_date:
            description += f"\nLast Event: {latest_event_date}"

        return CaseStatus(
            case_number=case_number,
            status=status + notice_info,
            title=f"{form_type} - {form_name}",
            description=description,
            form_type=form_type,
            received_date=received_date,
            last_updated=datetime.now()
        )
    except (KeyError, TypeError) as e:
        print(f"Error parsing authenticated case status for {case_number}: {e}")
        return None


def parse_similar_cases(cases: list[CaseStatus]) -> SimilarCasesSummary:
    """Analyze a list of similar cases and return a summary."""
    if not cases:
        return SimilarCasesSummary(
            base_case_number="",
            total_checked=0,
            status_counts={},
            approved_count=0,
            pending_count=0,
            denied_count=0
        )

    status_counts = {}
    approved_count = 0
    pending_count = 0
    denied_count = 0

    # Keywords to identify status categories
    approved_keywords = ['approved', 'card was produced', 'card was mailed',
                         'card was picked up', 'card is being produced']
    denied_keywords = ['denied', 'rejected', 'terminated']

    for case in cases:
        status_lower = case.status.lower()

        # Count by exact status
        status_counts[case.status] = status_counts.get(case.status, 0) + 1

        # Categorize
        if any(kw in status_lower for kw in approved_keywords):
            approved_count += 1
        elif any(kw in status_lower for kw in denied_keywords):
            denied_count += 1
        else:
            pending_count += 1

    return SimilarCasesSummary(
        base_case_number=cases[0].case_number if cases else "",
        total_checked=len(cases),
        status_counts=status_counts,
        approved_count=approved_count,
        pending_count=pending_count,
        denied_count=denied_count
    )
