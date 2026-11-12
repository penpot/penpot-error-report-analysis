"""Command-line entry point for backfill runs."""

from __future__ import annotations

import argparse
import logging
from datetime import UTC, datetime, timedelta

from error_analysis.backfill.workflow import InsightSeverityBackfill, ReportVersionBackfill
from error_analysis.context import AnalysisContext
from error_analysis.logging_config import configure_logging

log = logging.getLogger(__name__)


def main() -> None:
    """
    Enriches already-recorded data: the deployment version of classified reports, retrieved from the
    report source, and the severity of analyzed classes, derived from their insights.

    Both backfills are idempotent and leave data that is already present untouched.
    """
    # parse the command line
    parser = argparse.ArgumentParser(description="Backfills report versions and class severities onto already-recorded data.")
    parser.add_argument("--scope", choices=["all", "versions", "severities"], default="all", help="the data to backfill")
    parser.add_argument("--days", type=float, default=30.0, help="the number of past days whose reports are to be given their version")
    parser.add_argument("--reassess", action="store_true", help="derive severities anew for classes that already carry one")
    args = parser.parse_args()

    configure_logging()
    context = AnalysisContext.create_default()

    # record the deployment version of the window's reports
    if args.scope in ("all", "versions"):
        result = ReportVersionBackfill(context.provider, context.repository).run(since=datetime.now(UTC) - timedelta(days=args.days))
        log.info(
            "Versions: %d reports in window, %d with a version, %d associations updated",
            result.reports_seen,
            result.reports_with_version,
            result.associations_updated,
        )

    # derive the severity of the analyzed classes from their insights
    if args.scope in ("all", "severities"):
        severities = InsightSeverityBackfill(context.repository).run(overwrite=args.reassess)
        for assignment in severities.assignments:
            log.info("Class %d: %s", assignment.class_id, assignment.severity.value)
        for undecided in severities.undecided:
            log.info("Class %d: undecided, assess by hand — %s", undecided.class_id, undecided.statement)
        log.info(
            "Severities: %d assigned, %d undecided, %d retained",
            len(severities.assignments),
            len(severities.undecided),
            severities.retained,
        )
