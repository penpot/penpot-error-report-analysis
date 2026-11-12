"""Composition of GitHub issue drafts from analysis insights."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from urllib.parse import quote
from uuid import UUID

from error_analysis.persistence.records import EquivalenceClassRecord, InsightRecord


@dataclass(frozen=True)
class IssueDraft:
    """
    A GitHub issue prepared from an analysis insight.

    :ivar title: the issue title
    :ivar body: the issue body in markdown format
    :ivar form_url: the URL of GitHub's issue creation form with the title prefilled; the body is not
        passed within the URL, as issue bodies regularly exceed the length a URL can carry, and is
        therefore to be transferred via the clipboard
    """

    title: str
    body: str
    form_url: str


class IssueDraftFactory:
    """
    Creates GitHub issue drafts for analysis insights.

    The draft body comprises the insight's analysis report followed by a metadata section that
    identifies the equivalence class and points to concrete member reports, enabling a developer to
    retrieve the original reports.
    """

    _MAX_TITLE_LENGTH = 120
    """the maximum length of the generated issue title"""

    _DIGEST_PREFIX_LENGTH = 12
    """the number of digest characters included in the metadata section"""

    def __init__(self, repository_slug: str = "penpot/penpot") -> None:
        """
        :param repository_slug: the ``owner/name`` slug of the GitHub repository to file issues in
        """
        self._new_issue_url = f"https://github.com/{repository_slug}/issues/new"

    def create_draft(
        self,
        equivalence_class: EquivalenceClassRecord,
        insight: InsightRecord,
        report_count: int,
        member_report_ids: list[UUID],
    ) -> IssueDraft:
        """
        Creates the issue draft for the given insight.

        :param equivalence_class: the equivalence class the insight pertains to
        :param insight: the insight whose analysis report is to be filed
        :param report_count: the number of reports in the class within the considered time window
        :param member_report_ids: the identifiers of member reports to reference
        :return: the draft
        """
        # compose title and body
        title = self._create_title(equivalence_class)
        body = f"{insight.markdown.strip()}\n\n{self._create_metadata_section(equivalence_class, insight, report_count, member_report_ids)}"

        return IssueDraft(title=title, body=body, form_url=f"{self._new_issue_url}?title={quote(title)}")

    @classmethod
    def _create_title(cls, equivalence_class: EquivalenceClassRecord) -> str:
        """
        :param equivalence_class: the equivalence class to derive the title from
        :return: the issue title, derived from the class's exemplar hint
        """
        title = " ".join(equivalence_class.exemplar_hint.split())
        if len(title) > cls._MAX_TITLE_LENGTH:
            title = title[: cls._MAX_TITLE_LENGTH - 1].rstrip() + "…"
        return title or f"Error report class {equivalence_class.id}"

    @classmethod
    def _create_metadata_section(
        cls,
        equivalence_class: EquivalenceClassRecord,
        insight: InsightRecord,
        report_count: int,
        member_report_ids: list[UUID],
    ) -> str:
        """
        :param equivalence_class: the equivalence class the insight pertains to
        :param insight: the insight whose analysis report is to be filed
        :param report_count: the number of reports in the class within the considered time window
        :param member_report_ids: the identifiers of member reports to reference
        :return: the markdown metadata section appended to the analysis report
        """
        # summarize the class's identity and occurrence
        lines = [
            "---",
            "",
            "### Error report data",
            "",
            f"* Equivalence class: {equivalence_class.id} ({cls._describe_identity(equivalence_class)})",
            f"* Occurrences: {report_count} reports, "
            f"{cls._format_instant(equivalence_class.first_seen_at)} to {cls._format_instant(equivalence_class.last_seen_at)}",
        ]

        # reference the analyzed and further member reports
        lines.append(f"* Analyzed report: `{insight.analyzed_report_id}`")
        further_ids = [report_id for report_id in member_report_ids if report_id != insight.analyzed_report_id]
        if further_ids:
            lines.append("* Further member reports: " + ", ".join(f"`{report_id}`" for report_id in further_ids))

        # include the signature, which characterizes the error
        summary = "Class description" if equivalence_class.manually_created else "Fingerprint signature"
        lines.extend(["", "<details>", f"<summary>{summary}</summary>", "", "```", equivalence_class.signature, "```", "", "</details>"])

        # attribute the analysis
        author = insight.author or "an LLM"
        lines.extend(["", f"*Analysis generated by {author} on {cls._format_instant(insight.created_at)}.*"])

        return "\n".join(lines)

    @classmethod
    def _describe_identity(cls, equivalence_class: EquivalenceClassRecord) -> str:
        """
        :param equivalence_class: the equivalence class to describe
        :return: the parenthetical describing how the class is identified
        """
        if equivalence_class.manually_created:
            return "manually created"
        return f"fingerprint `{equivalence_class.digest[: cls._DIGEST_PREFIX_LENGTH]}`, algorithm v{equivalence_class.algorithm_version}"

    @staticmethod
    def _format_instant(instant: datetime) -> str:
        """
        :param instant: the instant to format
        :return: the instant as a date string
        """
        return instant.strftime("%Y-%m-%d")
