"""The abstract interface of the analysis database."""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Collection
from datetime import datetime
from uuid import UUID

from error_analysis.fingerprint.algorithm import Fingerprint
from error_analysis.persistence.records import ClassOverview, ClassResolution, EquivalenceClassRecord, InsightRecord


class AnalysisRepository(ABC):
    """
    The store for equivalence class associations and gathered insights.

    The repository deliberately does not store error reports themselves (these live in the Penpot
    database); it stores only class membership, per-class metadata caches, and analysis insights.
    """

    @abstractmethod
    def get_or_create_class(self, fingerprint: Fingerprint, exemplar_hint: str) -> ClassResolution:
        """
        Retrieves the equivalence class with the given fingerprint digest, creating it if it does not exist.

        :param fingerprint: the fingerprint identifying the class
        :param exemplar_hint: the raw hint of a member report, cached at class creation for display purposes
        :return: the resolution, carrying the class and whether it was newly created
        """

    @abstractmethod
    def associate(self, report_id: UUID, class_id: int, report_created_at: datetime, report_version: str | None = None) -> None:
        """
        Associates the given report with the given equivalence class, updating the class's
        first/last-seen instants accordingly. The operation is idempotent.

        :param report_id: the identifier of the report
        :param class_id: the identifier of the equivalence class
        :param report_created_at: the creation instant of the report
        :param report_version: the deployment version the report originates from, if known
        """

    @abstractmethod
    def associated_report_ids(self, report_ids: Collection[UUID]) -> set[UUID]:
        """
        Determines which of the given reports are already associated with an equivalence class.

        :param report_ids: the report identifiers to check
        :return: the subset of identifiers for which an association exists
        """

    @abstractmethod
    def member_report_ids(self, class_id: int, limit: int | None = None, newest_first: bool = True) -> list[tuple[UUID, datetime]]:
        """
        Retrieves the reports associated with the given equivalence class.

        :param class_id: the identifier of the equivalence class
        :param limit: the maximum number of reports to return; unbounded if ``None``
        :param newest_first: whether to order by report creation instant descending (rather than ascending)
        :return: pairs of report identifier and report creation instant
        """

    @abstractmethod
    def get_class(self, class_id: int) -> EquivalenceClassRecord | None:
        """
        :param class_id: the identifier of the equivalence class
        :return: the equivalence class, or ``None`` if no class with the given identifier exists
        """

    @abstractmethod
    def list_class_overviews(self, count_since: datetime | None = None, version: str | None = None) -> list[ClassOverview]:
        """
        Retrieves all equivalence classes together with aggregate statistics, ordered by report count descending.

        :param count_since: if given, only reports created after this instant contribute to the report counts,
            and classes without any such report are omitted
        :param version: if given, only reports originating from this deployment version contribute to the
            report counts, and classes without any such report are omitted
        :return: the class overviews
        """

    @abstractmethod
    def list_report_versions(self, since: datetime | None = None) -> list[str]:
        """
        Retrieves the deployment versions the associated reports originate from, newest first.

        Reports classified before version recording was introduced carry no version and are not represented.

        :param since: if given, only reports created after this instant are considered
        :return: the distinct versions
        """

    @abstractmethod
    def add_insight(self, class_id: int, analyzed_report_id: UUID, markdown: str, author: str | None = None) -> InsightRecord:
        """
        Stores an insight for the given equivalence class.

        :param class_id: the identifier of the equivalence class the insight pertains to
        :param analyzed_report_id: the identifier of the concrete report instance that was analyzed
        :param markdown: the insight content in markdown format
        :param author: the author of the insight (e.g. an LLM model name)
        :return: the stored insight
        """

    @abstractmethod
    def set_issue_number(self, class_id: int, issue_number: int | None) -> EquivalenceClassRecord:
        """
        Records the number of the GitHub issue filed for the given equivalence class.

        :param class_id: the identifier of the equivalence class
        :param issue_number: the issue number; ``None`` to remove a previously recorded number
        :return: the updated equivalence class
        :raises KeyError: if no class with the given identifier exists
        """

    @abstractmethod
    def list_insights(self, class_id: int) -> list[InsightRecord]:
        """
        Retrieves the insights stored for the given equivalence class, oldest first.

        :param class_id: the identifier of the equivalence class
        :return: the insights
        """
