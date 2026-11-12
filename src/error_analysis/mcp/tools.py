"""The MCP tools of the analysis platform."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta

from error_analysis.mcp.tools_base import Tool, ToolCallError
from error_analysis.persistence.records import ClassOverview, EquivalenceClassRecord, Severity
from error_analysis.persistence.repository import AnalysisRepository
from error_analysis.serialization import JsonSerializer


def _unanalyzed_overviews(repository: AnalysisRepository, since: datetime) -> list[ClassOverview]:
    """
    :param repository: the repository to query
    :param since: the window start that report counts refer to
    :return: the overviews of all unanalyzed classes with reports in the window, ordered by report
        count descending
    """
    return [o for o in repository.list_class_overviews(count_since=since) if o.insight_count == 0]


def _parse_severity(value: str) -> Severity:
    """
    :param value: the severity name to parse
    :return: the parsed severity
    :raises ToolCallError: if the value does not name a known severity
    """
    try:
        return Severity(value.strip().lower())
    except ValueError as e:
        raise ToolCallError(f"Invalid severity '{value}': must be one of {[s.value for s in Severity]}") from e


_BOOTSTRAP_INSTRUCTIONS = """\
Present the classes listed under 'top_unanalyzed_classes' to the user (rank, class id, hint, report count,
last seen) and ask them to decide how many of these classes shall be analyzed (or which specific class ids,
if they prefer to choose). Do not start any analysis yet. Once the user has decided, call
get_analysis_candidates with their choice to receive the classes' details and the analysis instructions."""

_ANALYSIS_INSTRUCTIONS = """\
You are to analyze Penpot error report equivalence classes. For each class listed under 'candidates' (in order):
1. Inspect the class's fingerprint signature (already included) to understand the error at a glance.
2. Retrieve the details of a concrete member report via get_report_details, using the most recent member id
   from 'recent_member_ids'. Retrieve further members via get_equivalence_class if one instance is inconclusive.
3. Investigate the root cause in the Penpot codebase using the code analysis tools at your disposal
   (symbol search, memories, REPL evaluation, git history).
4. Write a markdown report with the sections: Summary, Root Cause, Evidence, Affected Code,
   Suggested Fix, Severity. If the analysis remains inconclusive, report your findings and open questions instead.
5. Store the report via store_insight (passing the id of the report instance you analyzed, your model name as
   author, and the severity you assessed).
Then continue with the next class. Do not skip storing an insight for an analyzed class.
Filing GitHub issues is not part of this workflow; should the user explicitly ask for it (and a candidate's
'triage.issue_number' be absent, i.e. no issue filed yet), record the number of the issue you filed via
set_class_triage."""


class BootstrapAnalysisTool(Tool):
    """
    Bootstraps an analysis session.
    """

    _TOP_CLASS_COUNT = 10
    """the number of top unanalyzed classes presented for the user's decision"""

    _QUICK_INFO_HINT_LENGTH = 160
    """the maximum hint length in the quick class info"""

    def apply(self, days: int = 1, max_reports: int = 1000) -> str:
        """
        Classifies the error reports of the recent past into equivalence classes and returns an overview
        of the analysis-worthy (i.e. not yet analyzed) classes, upon which the user is to decide what
        shall be analyzed. This is the entry point of an analysis session; call it once and then follow
        the returned instructions.
        To bound the call's duration, at most the max_reports most recent not-yet-classified reports are
        processed; if this truncates the run, the result is marked accordingly and carries the coverage
        boundary (the window is fully classified from its newest end down to that boundary), and completing
        the window via the error-analysis-classify command line is advisable (afterwards, this tool is fast
        for any window).

        :param days: the number of past days whose reports are to be classified and counted
        :param max_reports: the maximum number of not-yet-classified reports to classify during this call
        :return: a JSON object carrying the classification run's statistics, quick information on the top
            unanalyzed classes, and instructions for the decision to request from the user
        """
        # associate the window's reports with equivalence classes, bounded by the report limit
        since = datetime.now(UTC) - timedelta(days=days)
        result = self._context.create_classifier().classify_window(since=since, max_new_reports=max_reports)

        # assemble quick information on the top unanalyzed classes for the user's decision
        unanalyzed = _unanalyzed_overviews(self._context.repository, since)
        top_classes = [self._quick_info(rank, overview) for rank, overview in enumerate(unanalyzed[: self._TOP_CLASS_COUNT], start=1)]

        # describe the classification run, flagging truncation
        classification_run: dict[str, object] = {
            "window_days": days,
            "reports_seen": result.reports_seen,
            "reports_pending": result.reports_pending,
            "reports_newly_classified": result.reports_classified,
            "classes_newly_created": result.classes_created,
            "truncated": result.truncated,
        }
        if result.truncated:
            classification_run["classified_down_to"] = result.classified_down_to.isoformat() if result.classified_down_to else None
            classification_run["note"] = (
                "The report limit truncated classification: the most recent reports were classified, so the "
                "window is fully covered from its newest end down to 'classified_down_to'; reports older than "
                "that boundary remain unclassified, making report counts lower bounds for the older part of "
                "the window. Complete the window via the error-analysis-classify command line if needed."
            )

        return self._to_json(
            {
                "classification_run": classification_run,
                "unanalyzed_class_count": len(unanalyzed),
                "top_unanalyzed_classes": top_classes,
                "instructions": _BOOTSTRAP_INSTRUCTIONS,
            }
        )

    @classmethod
    def _quick_info(cls, rank: int, overview: ClassOverview) -> dict[str, object]:
        """
        :param rank: the class's rank by report count
        :param overview: the class overview
        :return: the condensed class information presented for the user's decision
        """
        record = overview.equivalence_class
        hint = record.exemplar_hint
        if len(hint) > cls._QUICK_INFO_HINT_LENGTH:
            hint = hint[: cls._QUICK_INFO_HINT_LENGTH] + "…"
        return {
            "rank": rank,
            "class_id": record.id,
            "hint": hint,
            "report_count": overview.report_count,
            "last_seen_at": record.last_seen_at.isoformat(),
        }


class GetAnalysisCandidatesTool(Tool):
    """
    Retrieves the equivalence classes to be analyzed.
    """

    _MEMBER_ID_COUNT = 5
    """the number of recent member report ids included per candidate"""

    def apply(self, num_classes: int = 3, days: int = 1, class_ids: list[int] | None = None) -> str:
        """
        Retrieves the equivalence classes to be analyzed in full detail, together with the analysis
        workflow instructions. Call this after the user has decided (based on the bootstrap_analysis
        overview) what shall be analyzed: either the top num_classes unanalyzed classes, or, if the
        user chose specific classes, exactly those given via class_ids.

        :param num_classes: the number of top unanalyzed equivalence classes to retrieve; ignored if
            class_ids is given
        :param days: the number of past days that reports must fall into to be counted
        :param class_ids: the ids of the specific equivalence classes chosen by the user, if any
        :return: a JSON object carrying the analysis workflow instructions and the candidate classes
            with their signatures and recent member report ids
        """
        # select the candidate classes per the user's decision
        since = datetime.now(UTC) - timedelta(days=days)
        if class_ids:
            candidates = self._chosen_overviews(class_ids, since)
        else:
            candidates = _unanalyzed_overviews(self._context.repository, since)[:num_classes]

        # attach signatures and recent member report ids
        entries = []
        for overview in candidates:
            members = self._context.repository.member_report_ids(overview.equivalence_class.id, limit=self._MEMBER_ID_COUNT)
            entry = JsonSerializer.class_overview(overview)
            entry["recent_member_ids"] = [str(report_id) for report_id, _ in members]
            entries.append(entry)

        return self._to_json(
            {
                "instructions": _ANALYSIS_INSTRUCTIONS,
                "candidates": entries,
            }
        )

    def _chosen_overviews(self, class_ids: list[int], since: datetime) -> list[ClassOverview]:
        """
        Resolves explicitly chosen classes to overviews, honoring the user's choice regardless of
        analysis state.

        :param class_ids: the ids of the chosen equivalence classes
        :param since: the window start that report counts refer to
        :return: the overviews in the order chosen
        :raises ToolCallError: if any of the given ids does not exist
        """
        # index the in-window overviews for count lookup
        overviews_by_id = {o.equivalence_class.id: o for o in self._context.repository.list_class_overviews(count_since=since)}

        # resolve each chosen class, falling back to a zero-count overview for classes without reports in the window
        chosen = []
        for class_id in class_ids:
            if (overview := overviews_by_id.get(class_id)) is None:
                record = self._context.repository.get_class(class_id)
                if record is None:
                    raise ToolCallError(f"No equivalence class with id {class_id} exists")
                overview = ClassOverview(
                    equivalence_class=record, report_count=0, insight_count=len(self._context.repository.list_insights(class_id))
                )
            chosen.append(overview)
        return chosen


class ListClassOverviewsTool(Tool):
    """
    Lists equivalence class overviews.
    """

    def apply(self, days: int = 30) -> str:
        """
        Lists all equivalence classes with reports in the recent past, ordered by report count descending.
        Note that report counts reflect the given time window, and only classes with reports in the window are listed.

        :param days: the number of past days that reports must fall into to be counted
        :return: a JSON array of class overviews
        """
        since = datetime.now(UTC) - timedelta(days=days)
        overviews = self._context.repository.list_class_overviews(count_since=since)
        return self._to_json([JsonSerializer.class_overview(o) for o in overviews])


class GetEquivalenceClassTool(Tool):
    """
    Retrieves an equivalence class.
    """

    def apply(self, class_id: int, member_limit: int = 20) -> str:
        """
        Retrieves an equivalence class, including its fingerprint signature, stored insights,
        and the ids of its most recent member reports.

        :param class_id: the id of the equivalence class
        :param member_limit: the maximum number of recent member report ids to include
        :return: a JSON object describing the class
        """
        # retrieve the class
        record = self._context.repository.get_class(class_id)
        if record is None:
            raise ToolCallError(f"No equivalence class with id {class_id} exists")

        # assemble the class description with members and insights
        members = self._context.repository.member_report_ids(class_id, limit=member_limit)
        insights = self._context.repository.list_insights(class_id)
        return self._to_json(
            {
                **JsonSerializer.equivalence_class(record),
                "recent_members": [
                    {"report_id": str(report_id), "created_at": created_at.isoformat()} for report_id, created_at in members
                ],
                "insights": [JsonSerializer.insight(i) for i in insights],
            }
        )


class GetReportDetailsTool(Tool):
    """
    Retrieves the details of an error report.
    """

    def apply(self, report_id: str) -> str:
        """
        Retrieves the full details of an error report, including its stack trace and context metadata.

        :param report_id: the id (UUID) of the error report
        :return: a JSON object with the report details
        """
        report = self._context.provider.get_report(self._parse_uuid(report_id))
        return self._to_json(JsonSerializer.report(report))


class StoreInsightTool(Tool):
    """
    Stores an analysis insight.
    """

    def apply(self, class_id: int, analyzed_report_id: str, markdown: str, author: str = "", severity: str = "") -> str:
        """
        Stores a markdown analysis report as an insight for an equivalence class, optionally recording
        the severity you assessed on the class itself.

        :param class_id: the id of the equivalence class the insight pertains to
        :param analyzed_report_id: the id (UUID) of the concrete report instance that was analyzed
        :param markdown: the analysis report in markdown format (sections: Summary, Root Cause, Evidence,
            Affected Code, Suggested Fix, Severity)
        :param author: the author of the insight, e.g. the analyzing LLM's model name
        :param severity: the assessed severity of the underlying defect: one of critical, high, medium, low;
            leave empty to keep the severity recorded so far
        :return: a JSON object describing the stored insight
        """
        # validate the referenced class
        record = self._context.repository.get_class(class_id)
        if record is None:
            raise ToolCallError(f"No equivalence class with id {class_id} exists")

        # record the assessed severity on the class, retaining the remaining triage state
        if severity:
            self._context.repository.set_triage(class_id, replace(record.triage, severity=_parse_severity(severity)))

        # store the insight
        insight = self._context.repository.add_insight(
            class_id=class_id,
            analyzed_report_id=self._parse_uuid(analyzed_report_id),
            markdown=markdown,
            author=author or None,
        )
        return self._to_json(JsonSerializer.insight(insight))


class _ClassCurationTool(Tool):
    """
    The base of the tools curating the composition of equivalence classes.
    """

    def _move_reports(self, report_ids: list[str], target_class_id: int) -> dict[str, object]:
        """
        Moves the given reports to the given equivalence class.

        :param report_ids: the ids (UUIDs) of the reports to move
        :param target_class_id: the id of the equivalence class the reports are to belong to
        :return: the JSON-compatible summary of the move
        :raises ToolCallError: if an id is malformed, the class does not exist, or a report is not
            associated with any class; in these cases, no report is moved
        """
        try:
            move = self._context.repository.move_reports([self._parse_uuid(report_id) for report_id in report_ids], target_class_id)
        except KeyError as e:
            raise ToolCallError(str(e.args[0])) from e
        return {
            "target_class_id": move.target_class.id,
            "moved_report_ids": [str(report_id) for report_id in move.moved_report_ids],
            "already_in_target_class": [str(report_id) for report_id in move.retained_report_ids],
            "source_class_ids": list(move.source_class_ids),
            "emptied_class_ids": list(move.emptied_class_ids),
        }


class CreateEquivalenceClassTool(_ClassCurationTool):
    """
    Creates a manually curated equivalence class.
    """

    def apply(self, hint: str, description: str = "", report_ids: list[str] | None = None) -> str:
        """
        Creates a new equivalence class that is not derived from a fingerprint and moves the given reports
        into it. Use this to split reports that the fingerprint algorithm grouped together although they
        pertain to different defects; to merge classes, move the reports of one class into another via
        move_reports instead.
        Note that classification keeps following the fingerprints: it never assigns reports to a manually
        created class, so later reports of the same kind arrive in the class the given reports were taken
        from, and moving them here is to be repeated.

        :param hint: the hint characterizing the class, displayed wherever classes are listed
        :param description: the description of what unites the class, taking the place of the fingerprint
            signature that an automatically created class carries
        :param report_ids: the ids (UUIDs) of the reports to move into the new class
        :return: a JSON object describing the created class and the reports moved into it
        """
        # create the class, which is empty at first
        record = self._context.repository.create_manual_class(hint=hint, signature=description)

        # populate it with the given reports
        result: dict[str, object] = {"equivalence_class": JsonSerializer.equivalence_class(record)}
        if report_ids:
            result["move"] = self._move_reports(report_ids, record.id)
            result["equivalence_class"] = JsonSerializer.equivalence_class(self._reloaded(record.id))
        return self._to_json(result)

    def _reloaded(self, class_id: int) -> EquivalenceClassRecord:
        """
        :param class_id: the id of the equivalence class
        :return: the class as recorded after the move, whose seen instants cover the moved reports
        :raises ToolCallError: if the class no longer exists
        """
        record = self._context.repository.get_class(class_id)
        if record is None:
            raise ToolCallError(f"No equivalence class with id {class_id} exists")
        return record


class MoveReportsTool(_ClassCurationTool):
    """
    Moves error reports between equivalence classes.
    """

    def apply(self, report_ids: list[str], target_class_id: int) -> str:
        """
        Moves the given error reports to the given equivalence class, taking them out of the classes they
        belong to so far. Use this to correct the classification: to merge two classes that pertain to the
        same defect (moving all member reports of one class into the other, which leaves the source class
        empty), or to move individual reports that were grouped with an unrelated defect. Reports already
        belonging to the target class are left alone; if any of the given reports is not classified at all,
        no report is moved.
        Note that classification keeps following the fingerprints: later reports of the moved kind arrive
        in the class they were taken from, so the move is to be repeated for them.

        :param report_ids: the ids (UUIDs) of the reports to move
        :param target_class_id: the id of the equivalence class the reports are to belong to
        :return: a JSON object describing the move, including the source classes left without members
        """
        return self._to_json(self._move_reports(report_ids, target_class_id))


class SetClassTriageTool(Tool):
    """
    Records the triage state of an equivalence class.
    """

    def apply(
        self,
        class_id: int,
        severity: str = "",
        issue_number: int | None = None,
        pr_number: int | None = None,
        fixed: bool | None = None,
    ) -> str:
        """
        Records the triage state of an equivalence class: the assessed severity of the underlying defect,
        the numbers of the GitHub issue and the pull request addressing it, and whether it is fixed.
        Only the arguments you pass take effect; the state recorded so far is retained otherwise.

        :param class_id: the id of the equivalence class
        :param severity: the assessed severity: one of critical, high, medium, low; pass 'none' to remove
            the assessment
        :param issue_number: the number of the GitHub issue filed for the class; pass 0 to remove the
            recorded number
        :param pr_number: the number of the pull request addressing the class; pass 0 to remove the
            recorded number
        :param fixed: whether the underlying defect has been fixed; note that the reports of a fixed defect
            keep appearing in time windows that precede the fix
        :return: a JSON object describing the class with its updated triage state
        """
        # validate the referenced class
        record = self._context.repository.get_class(class_id)
        if record is None:
            raise ToolCallError(f"No equivalence class with id {class_id} exists")

        # apply the given modifications to the triage state recorded so far
        triage = record.triage
        if severity:
            triage = replace(triage, severity=None if severity.strip().lower() == "none" else _parse_severity(severity))
        if issue_number is not None:
            triage = replace(triage, issue_number=issue_number or None)
        if pr_number is not None:
            triage = replace(triage, pr_number=pr_number or None)
        if fixed is not None:
            triage = replace(triage, fixed=fixed)

        return self._to_json(JsonSerializer.equivalence_class(self._context.repository.set_triage(class_id, triage)))
