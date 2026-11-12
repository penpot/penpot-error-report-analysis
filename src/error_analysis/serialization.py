"""JSON serialization of domain objects for delivery layers."""

from __future__ import annotations

from typing import Any

from error_analysis.persistence.records import ClassOverview, ClassTriage, EquivalenceClassRecord, InsightRecord
from error_analysis.reports.model import BackendErrorReport, ErrorReport, FrontendErrorReport


class JsonSerializer:
    """
    Converts domain objects into JSON-compatible dictionaries.

    The representations are shared by the MCP server and the web backend so that both delivery
    layers expose identical structures.
    """

    @staticmethod
    def triage(triage: ClassTriage) -> dict[str, Any]:
        """
        :param triage: the triage state to serialize
        :return: the JSON-compatible representation
        """
        return {
            "severity": triage.severity.value if triage.severity is not None else None,
            "issue_number": triage.issue_number,
            "pr_number": triage.pr_number,
            "fixed": triage.fixed,
        }

    @classmethod
    def equivalence_class(cls, record: EquivalenceClassRecord) -> dict[str, Any]:
        """
        :param record: the equivalence class to serialize
        :return: the JSON-compatible representation
        """
        return {
            "id": record.id,
            "digest": record.digest,
            "signature": record.signature,
            "algorithm_version": record.algorithm_version,
            "manually_created": record.manually_created,
            "exemplar_hint": record.exemplar_hint,
            "first_seen_at": record.first_seen_at.isoformat(),
            "last_seen_at": record.last_seen_at.isoformat(),
            "triage": cls.triage(record.triage),
        }

    @classmethod
    def class_overview(cls, overview: ClassOverview) -> dict[str, Any]:
        """
        :param overview: the class overview to serialize
        :return: the JSON-compatible representation
        """
        return {
            **cls.equivalence_class(overview.equivalence_class),
            "report_count": overview.report_count,
            "insight_count": overview.insight_count,
        }

    @staticmethod
    def insight(record: InsightRecord) -> dict[str, Any]:
        """
        :param record: the insight to serialize
        :return: the JSON-compatible representation
        """
        return {
            "id": record.id,
            "class_id": record.class_id,
            "analyzed_report_id": str(record.analyzed_report_id),
            "markdown": record.markdown,
            "author": record.author,
            "created_at": record.created_at.isoformat(),
        }

    @staticmethod
    def report(report: ErrorReport) -> dict[str, Any]:
        """
        :param report: the error report to serialize
        :return: the JSON-compatible representation, including the shape-specific detail fields
        """
        # serialize the shape-independent fields
        result: dict[str, Any] = {
            "id": str(report.id),
            "created_at": report.created_at.isoformat(),
            "source": report.source.value,
            "hint": report.hint,
            "context_edn": report.context_edn,
        }

        # append the shape-specific fields
        match report:
            case BackendErrorReport():
                result["shape"] = "backend"
                result["trace"] = report.trace
                result["props_edn"] = report.props_edn
                result["params_edn"] = report.params_edn
            case FrontendErrorReport():
                result["shape"] = "frontend"
                result["kind"] = report.kind
                result["origin"] = report.origin
                result["href"] = report.href
                result["data_section"] = report.data_section
                result["trace_section"] = report.trace_section
        return result
