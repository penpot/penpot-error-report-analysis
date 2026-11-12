"""Domain records exposed by the persistence layer."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from uuid import UUID


class Severity(Enum):
    """
    The assessed severity of the defect underlying an equivalence class.
    """

    CRITICAL = "critical"
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


@dataclass(frozen=True)
class ClassTriage:
    """
    The curated triage state of an equivalence class: the assessed severity and the progress of the
    underlying defect's resolution.

    The resolution markers are independent of one another: a pull request may exist without a filed
    issue, and a class may be marked as fixed while its reports keep appearing in older time windows.

    :ivar severity: the assessed severity, if one has been assigned
    :ivar issue_number: the number of the GitHub issue filed for the class, if any
    :ivar pr_number: the number of the pull request addressing the class, if any
    :ivar fixed: whether the underlying defect has been fixed
    """

    severity: Severity | None = None
    issue_number: int | None = None
    pr_number: int | None = None
    fixed: bool = False


@dataclass(frozen=True)
class EquivalenceClassRecord:
    """
    A persisted equivalence class of error reports.

    :ivar id: the surrogate identifier of the class
    :ivar digest: the fingerprint digest identifying the class
    :ivar signature: the signature document from which the digest was derived
    :ivar algorithm_version: the version of the fingerprint algorithm that produced the digest
    :ivar exemplar_hint: the raw hint of one member report, cached for display purposes
    :ivar first_seen_at: the creation instant of the oldest associated report
    :ivar last_seen_at: the creation instant of the newest associated report
    :ivar triage: the curated triage state of the class
    """

    id: int
    digest: str
    signature: str
    algorithm_version: int
    exemplar_hint: str
    first_seen_at: datetime
    last_seen_at: datetime
    triage: ClassTriage = ClassTriage()


@dataclass(frozen=True)
class ClassResolution:
    """
    The result of resolving a fingerprint to an equivalence class.

    :ivar equivalence_class: the resolved equivalence class
    :ivar created: whether the class was newly created during resolution
    """

    equivalence_class: EquivalenceClassRecord
    created: bool


@dataclass(frozen=True)
class ClassOverview:
    """
    An equivalence class together with aggregate statistics.

    :ivar equivalence_class: the equivalence class
    :ivar report_count: the number of associated reports (within the queried time window, if one was given)
    :ivar insight_count: the number of insights stored for the class
    """

    equivalence_class: EquivalenceClassRecord
    report_count: int
    insight_count: int


@dataclass(frozen=True)
class InsightRecord:
    """
    A persisted insight gathered for an equivalence class.

    :ivar id: the surrogate identifier of the insight
    :ivar class_id: the identifier of the equivalence class the insight pertains to
    :ivar analyzed_report_id: the identifier of the concrete report instance that was analyzed
    :ivar markdown: the insight content in markdown format
    :ivar author: the author of the insight (e.g. an LLM model name), if recorded
    :ivar created_at: the instant at which the insight was stored
    """

    id: int
    class_id: int
    analyzed_report_id: UUID
    markdown: str
    author: str | None
    created_at: datetime
