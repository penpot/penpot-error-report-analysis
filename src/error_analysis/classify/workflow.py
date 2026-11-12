"""The workflow associating error reports with equivalence classes."""

from __future__ import annotations

import logging
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import datetime, timedelta

from error_analysis.fingerprint.algorithm import FingerprintAlgorithm
from error_analysis.persistence.repository import AnalysisRepository
from error_analysis.reports.client import ReportProvider

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class ClassificationResult:
    """
    The outcome of a classification run.

    :ivar reports_seen: the number of reports found within the classified time window
    :ivar reports_pending: the number of reports within the window that required classification
    :ivar reports_classified: the number of reports that were newly associated with an equivalence class
    :ivar classes_created: the number of equivalence classes that were newly created
    :ivar truncated: whether classification was truncated by the report limit (leaving some of the
        window's reports unclassified)
    :ivar classified_down_to: the coverage boundary of a truncated run: the creation time of the oldest
        report classified in this run. Every report in the window newer than this boundary is classified;
        older reports may remain unclassified. ``None`` if the run was not truncated (the whole window
        is classified) or classified no reports.
    """

    reports_seen: int
    reports_pending: int
    reports_classified: int
    classes_created: int
    truncated: bool
    classified_down_to: datetime | None


class ReportClassifier:
    """
    Associates error reports with equivalence classes.

    For each not-yet-classified report within a time window, the full report is retrieved,
    fingerprinted, and associated with the matching equivalence class (which is created on demand).
    Already-classified reports are skipped without retrieving their details.
    """

    _PROGRESS_INTERVAL = 500
    """the number of processed reports between progress log messages"""

    def __init__(
        self, provider: ReportProvider, algorithm: FingerprintAlgorithm, repository: AnalysisRepository, fetch_concurrency: int = 8
    ) -> None:
        """
        :param provider: the source of error reports
        :param algorithm: the fingerprint algorithm determining class membership
        :param repository: the store for classes and associations
        :param fetch_concurrency: the number of report detail retrievals to perform concurrently
        """
        self._provider = provider
        self._algorithm = algorithm
        self._repository = repository
        self._fetch_concurrency = fetch_concurrency

    def classify_window(self, since: datetime, until: datetime | None = None, max_new_reports: int | None = None) -> ClassificationResult:
        """
        Classifies the reports within the given time window, newest first.

        :param since: the oldest boundary of the window (exclusive)
        :param until: the newest boundary of the window (exclusive); unbounded if ``None``
        :param max_new_reports: the maximum number of reports to classify, bounding the run's duration;
            unbounded if ``None``. If the limit truncates the run, the newest pending reports are
            classified, and the result is marked as truncated and carries the coverage boundary.
        :return: the outcome of the run
        """
        # collect the report summaries within the window and drop already-classified reports
        summaries = list(self._provider.iter_summaries(since=since, until=until))
        already_classified = self._repository.associated_report_ids([s.id for s in summaries])
        pending = sorted((s for s in summaries if s.id not in already_classified), key=lambda s: s.created_at, reverse=True)

        # bound the run by the report limit, preferring the newest reports
        truncated = max_new_reports is not None and len(pending) > max_new_reports
        to_classify = pending[:max_new_reports] if truncated else pending
        log.info(
            "Classifying %d of %d reports in window (%d already classified%s)",
            len(to_classify),
            len(summaries),
            len(already_classified),
            f", truncated from {len(pending)} pending" if truncated else "",
        )

        # fingerprint and associate each pending report, retrieving report details concurrently
        # (the details are paired with their summary, which carries the deployment version)
        classes_created = 0
        start = time.monotonic()
        with ThreadPoolExecutor(max_workers=self._fetch_concurrency, thread_name_prefix="report-fetch") as executor:
            reports = executor.map(self._provider.get_report, (s.id for s in to_classify))
            for index, (summary, report) in enumerate(zip(to_classify, reports, strict=True), start=1):
                fingerprint = self._algorithm.fingerprint(report)
                resolution = self._repository.get_or_create_class(fingerprint, exemplar_hint=report.hint)
                if resolution.created:
                    classes_created += 1
                self._repository.associate(
                    report.id,
                    resolution.equivalence_class.id,
                    report_created_at=report.created_at,
                    report_version=summary.version,
                )
                self._log_progress(index, len(to_classify), start)

        return ClassificationResult(
            reports_seen=len(summaries),
            reports_pending=len(pending),
            reports_classified=len(to_classify),
            classes_created=classes_created,
            truncated=truncated,
            classified_down_to=to_classify[-1].created_at if truncated and to_classify else None,
        )

    @classmethod
    def _log_progress(cls, processed: int, total: int, start_monotonic: float) -> None:
        """
        Logs classification progress at regular intervals.

        :param processed: the number of reports processed so far
        :param total: the total number of reports to process
        :param start_monotonic: the monotonic timestamp at which processing started
        """
        if processed % cls._PROGRESS_INTERVAL != 0 and processed != total:
            return
        rate = processed / max(time.monotonic() - start_monotonic, 1e-9)
        remaining = timedelta(seconds=int((total - processed) / max(rate, 1e-9)))
        log.info("Classified %d/%d reports (%.1f reports/s, ~%s remaining)", processed, total, rate, remaining)
