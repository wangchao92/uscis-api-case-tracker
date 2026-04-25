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

    try:
        data = response_data.get('data', response_data)

        form_type = data.get('formType', '')
        form_name = data.get('formName', '')
        received_date = data.get('submissionDate', '')
        updated_at = data.get('updatedAt', '')
        applicant_name = data.get('applicantName', '')

        # Get most recent event. Report the raw event code — USCIS event-code
        # meanings aren't publicly documented and have shifted over time, so
        # we leave interpretation to the user (look up the code on the portal).
        events = data.get('events', [])
        status = 'No events yet'
        latest_event_date = ''

        if events:
            sorted_events = sorted(
                events,
                key=lambda e: e.get('createdAtTimestamp', e.get('createdAt', '')),
                reverse=True
            )
            if sorted_events:
                latest_event = sorted_events[0]
                event_code = latest_event.get('eventCode', '')
                status = f'Event: {event_code}' if event_code else 'Unknown event'
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


