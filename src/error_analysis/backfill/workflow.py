"""The workflows enriching already-classified reports and already-analyzed classes."""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, replace
from datetime import datetime

from error_analysis.persistence.records import InsightRecord, Severity
from error_analysis.persistence.repository import AnalysisRepository
from error_analysis.reports.client import ReportProvider

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class VersionBackfillResult:
    """
    The outcome of a deployment version backfill.

    :ivar reports_seen: the number of reports found within the backfilled time window
    :ivar reports_with_version: the number of those reports the report source states a version for
    :ivar associations_updated: the number of associations that were given a version
    """

    reports_seen: int
    reports_with_version: int
    associations_updated: int


class ReportVersionBackfill:
    """
    Records the deployment version on associations created before versions were recorded.

    The version is carried by the report listing, so the backfill pages report summaries only and
    does not retrieve report details, which makes it substantially cheaper than a classification run.
    Reports the source no longer retains cannot be covered and keep their association without a version.
    """

    def __init__(self, provider: ReportProvider, repository: AnalysisRepository) -> None:
        """
        :param provider: the source of error reports
        :param repository: the store holding the associations to enrich
        """
        self._provider = provider
        self._repository = repository

    def run(self, since: datetime, until: datetime | None = None) -> VersionBackfillResult:
        """
        Backfills the versions of the reports within the given time window.

        :param since: the oldest boundary of the window (exclusive)
        :param until: the newest boundary of the window (exclusive); unbounded if ``None``
        :return: the outcome of the run
        """
        # collect the version of each report the window's listing states one for
        summaries = list(self._provider.iter_summaries(since=since, until=until))
        versions = {summary.id: summary.version for summary in summaries if summary.version}
        log.info("Found %d reports in window, %d of which state a version", len(summaries), len(versions))

        # record them on the associations that lack a version
        updated = self._repository.record_report_versions(versions)
        return VersionBackfillResult(reports_seen=len(summaries), reports_with_version=len(versions), associations_updated=updated)


@dataclass(frozen=True)
class SeverityAssignment:
    """
    A severity derived from an insight and assigned to its equivalence class.

    :ivar class_id: the identifier of the equivalence class
    :ivar severity: the assigned severity
    """

    class_id: int
    severity: Severity


@dataclass(frozen=True)
class UndecidedClass:
    """
    An equivalence class whose severity could not be derived unambiguously.

    :ivar class_id: the identifier of the equivalence class
    :ivar statement: the severity statement of the class's newest insight, quoted for a human decision
    """

    class_id: int
    statement: str


@dataclass(frozen=True)
class SeverityBackfillResult:
    """
    The outcome of a severity backfill.

    :ivar assignments: the classes that were given a severity
    :ivar undecided: the classes whose severity statement is ambiguous, left for a human to assess
    :ivar retained: the number of classes whose already-recorded severity was kept
    """

    assignments: list[SeverityAssignment]
    undecided: list[UndecidedClass]
    retained: int


class InsightSeverityBackfill:
    """
    Derives the severity of analyzed equivalence classes from the severity section of their insights.

    The severity of a class is an assessment rather than report data; it is stated in the ``Severity``
    section that analyses carry, which opens with the verdict and justifies it thereafter. The verdict
    is therefore read from the section's opening; a differing severity named in the immediate
    justification qualifies it (as in "medium for user impact, high for signal quality"), upon which
    the class is reported as undecided rather than assigned a severity the analysis does not state.
    Severities named further into the prose compare the class with others and do not bear on its own
    assessment. Each class is judged by its newest insight, that being the current state of the analysis.
    """

    _SECTION_HEADING = re.compile(r"^#{1,6}[ \t]*severity\b[^\n]*\n", re.IGNORECASE | re.MULTILINE)
    """matches the heading introducing an insight's severity section"""

    _FOLLOWING_HEADING = re.compile(r"^#{1,6}[ \t]", re.MULTILINE)
    """matches the heading that terminates the severity section"""

    _EMPHASIS = re.compile(r"[*_`]+")
    """matches the markdown emphasis markers surrounding a severity word"""

    _VERDICT_WINDOW = 150
    """the number of opening characters of a severity statement that qualify its verdict"""

    _STATEMENT_LENGTH = 160
    """the number of characters of a severity statement quoted for undecided classes"""

    def __init__(self, repository: AnalysisRepository) -> None:
        """
        :param repository: the store holding the classes and insights
        """
        self._repository = repository

    def run(self, overwrite: bool = False) -> SeverityBackfillResult:
        """
        Assigns severities to the analyzed classes that do not carry one.

        :param overwrite: whether to also reassess classes that already carry a severity
        :return: the outcome of the run
        """
        assignments: list[SeverityAssignment] = []
        undecided: list[UndecidedClass] = []
        retained = 0

        for overview in self._repository.list_class_overviews():
            record = overview.equivalence_class

            # keep the severity of classes that carry one, unless a reassessment was requested
            if record.triage.severity is not None and not overwrite:
                retained += 1
                continue

            # judge the class by its newest insight, if it has been analyzed at all
            insights = self._repository.list_insights(record.id)
            if not insights:
                continue
            statement = self._severity_statement(insights[-1])
            severity = self._leading_severity(statement)
            if severity is None:
                undecided.append(UndecidedClass(class_id=record.id, statement=self._quote(statement)))
                continue

            # record the derived severity, retaining the rest of the triage state
            self._repository.set_triage(record.id, replace(record.triage, severity=severity))
            assignments.append(SeverityAssignment(class_id=record.id, severity=severity))

        log.info("Assigned %d severities, left %d classes undecided, retained %d", len(assignments), len(undecided), retained)
        return SeverityBackfillResult(assignments=assignments, undecided=undecided, retained=retained)

    @classmethod
    def _severity_statement(cls, insight: InsightRecord) -> str:
        """
        :param insight: the insight to read
        :return: the content of the insight's severity section as a single line, emptied of emphasis
            markers; empty if the insight carries no such section
        """
        heading = cls._SECTION_HEADING.search(insight.markdown)
        if heading is None:
            return ""
        remainder = insight.markdown[heading.end() :]
        following = cls._FOLLOWING_HEADING.search(remainder)
        section = remainder[: following.start()] if following is not None else remainder
        return " ".join(cls._EMPHASIS.sub("", section).split())

    @classmethod
    def _leading_severity(cls, statement: str) -> Severity | None:
        """
        :param statement: the severity statement to interpret
        :return: the severity the statement's verdict names, or ``None`` if it opens with no severity
            or its opening qualifies the verdict with a differing one
        """
        # read the verdict the statement opens with
        verdict = next((s for s in Severity if re.match(rf"{s.value}\b", statement, re.IGNORECASE)), None)
        if verdict is None:
            return None

        # discard it if the immediate justification names a differing severity
        opening = statement[: cls._VERDICT_WINDOW]
        qualified = any(s is not verdict and re.search(rf"\b{s.value}\b", opening, re.IGNORECASE) for s in Severity)
        return None if qualified else verdict

    @classmethod
    def _quote(cls, statement: str) -> str:
        """
        :param statement: the severity statement to quote
        :return: the statement's opening, truncated for display
        """
        if len(statement) > cls._STATEMENT_LENGTH:
            return statement[: cls._STATEMENT_LENGTH - 1].rstrip() + "…"
        return statement or "(no severity section)"
