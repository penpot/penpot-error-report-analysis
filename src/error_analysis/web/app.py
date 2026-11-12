"""The Flask backend serving the dashboard and its JSON API."""

from __future__ import annotations

import argparse
import logging
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

from flask import Flask, Response, jsonify, request

from error_analysis.context import AnalysisContext
from error_analysis.logging_config import configure_logging
from error_analysis.serialization import JsonSerializer

log = logging.getLogger(__name__)


class WebBackend:
    """
    The web application of the analysis platform.

    Serves the static dashboard at the root path and provides the JSON endpoints the dashboard
    consumes: class overviews, class details (with insights and members), and report details.
    """

    _DEFAULT_WINDOW_DAYS = 30
    """the time window applied when the client does not request a specific one"""

    _ISSUE_MEMBER_COUNT = 5
    """the number of member reports referenced in a GitHub issue draft"""

    def __init__(self, context: AnalysisContext) -> None:
        """
        :param context: the platform services the endpoints operate on
        """
        self._context = context
        self._app = Flask(__name__)
        self._register_routes()

    @property
    def flask_app(self) -> Flask:
        """
        The configured Flask application.
        """
        return self._app

    def _register_routes(self) -> None:
        """
        Registers all routes with the Flask application.
        """
        self._app.add_url_rule("/", view_func=self._index)
        self._app.add_url_rule("/api/classes", view_func=self._list_classes)
        self._app.add_url_rule("/api/versions", view_func=self._list_versions)
        self._app.add_url_rule("/api/classes/<int:class_id>", view_func=self._get_class)
        self._app.add_url_rule("/api/classes/<int:class_id>/issue", view_func=self._set_issue_number, methods=["PUT"])
        self._app.add_url_rule("/api/classes/<int:class_id>/insights/<int:insight_id>/issue-draft", view_func=self._get_issue_draft)
        self._app.add_url_rule("/api/reports/<report_id>", view_func=self._get_report)

    def _index(self) -> Response:
        return self._app.send_static_file("index.html")

    def _list_classes(self) -> Any:
        """
        Delivers the class overviews for the requested time window (query parameter ``days``) and,
        optionally, a single deployment version (query parameter ``version``), ordered by report
        count descending.
        """
        # determine the requested window and version
        try:
            since = self._requested_window()
        except ValueError as e:
            return jsonify({"error": str(e)}), 400
        version = request.args.get("version") or None

        # deliver the overviews
        overviews = self._context.repository.list_class_overviews(count_since=since, version=version)
        return jsonify([JsonSerializer.class_overview(o) for o in overviews])

    def _list_versions(self) -> Any:
        """
        Delivers the deployment versions the reports of the requested time window (query parameter
        ``days``) originate from, newest first.
        """
        try:
            since = self._requested_window()
        except ValueError as e:
            return jsonify({"error": str(e)}), 400
        return jsonify(self._context.repository.list_report_versions(since=since))

    def _get_class(self, class_id: int) -> Any:
        """
        Delivers the details of an equivalence class: the class itself, its insights, and its
        most recent members.
        """
        record = self._context.repository.get_class(class_id)
        if record is None:
            return jsonify({"error": f"no equivalence class with id {class_id}"}), 404

        members = self._context.repository.member_report_ids(class_id, limit=50)
        insights = self._context.repository.list_insights(class_id)
        return jsonify(
            {
                **JsonSerializer.equivalence_class(record),
                "members": [{"report_id": str(report_id), "created_at": created_at.isoformat()} for report_id, created_at in members],
                "insights": [JsonSerializer.insight(i) for i in insights],
            }
        )

    def _set_issue_number(self, class_id: int) -> Any:
        """
        Records the number of the GitHub issue filed for an equivalence class, as given by the
        request body's ``issue_number`` entry (``null`` removing a previously recorded number).
        """
        # extract and validate the issue number
        payload = request.get_json(silent=True) or {}
        raw_number = payload.get("issue_number")
        if raw_number is None:
            issue_number = None
        else:
            try:
                issue_number = int(raw_number)
            except (TypeError, ValueError):
                return jsonify({"error": "'issue_number' must be an integer or null"}), 400
            if issue_number <= 0:
                return jsonify({"error": "'issue_number' must be positive"}), 400

        # record it
        try:
            record = self._context.repository.set_issue_number(class_id, issue_number)
        except KeyError:
            return jsonify({"error": f"no equivalence class with id {class_id}"}), 404
        return jsonify(JsonSerializer.equivalence_class(record))

    def _get_issue_draft(self, class_id: int, insight_id: int) -> Any:
        """
        Delivers the GitHub issue draft for an insight: title, body, and the URL of GitHub's issue
        creation form with the content prefilled.
        """
        # resolve the class and the insight
        record = self._context.repository.get_class(class_id)
        if record is None:
            return jsonify({"error": f"no equivalence class with id {class_id}"}), 404
        insight = next((i for i in self._context.repository.list_insights(class_id) if i.id == insight_id), None)
        if insight is None:
            return jsonify({"error": f"no insight with id {insight_id} for class {class_id}"}), 404

        # compose the draft from the class, the insight, and its member reports
        overview = next((o for o in self._context.repository.list_class_overviews() if o.equivalence_class.id == class_id), None)
        members = self._context.repository.member_report_ids(class_id, limit=self._ISSUE_MEMBER_COUNT)
        draft = self._context.issue_draft_factory.create_draft(
            equivalence_class=record,
            insight=insight,
            report_count=overview.report_count if overview is not None else 0,
            member_report_ids=[report_id for report_id, _ in members],
        )
        return jsonify({"title": draft.title, "body": draft.body, "form_url": draft.form_url})

    def _get_report(self, report_id: str) -> Any:
        """
        Delivers the full details of an error report, retrieved live from the report source.
        """
        try:
            parsed_id = UUID(report_id)
        except ValueError:
            return jsonify({"error": f"invalid report id '{report_id}'"}), 400
        return jsonify(JsonSerializer.report(self._context.provider.get_report(parsed_id)))

    def _requested_window(self) -> datetime:
        """
        :return: the start of the time window requested via the ``days`` query parameter
        :raises ValueError: if the parameter is not an integer
        """
        try:
            days = int(request.args.get("days", self._DEFAULT_WINDOW_DAYS))
        except ValueError as e:
            raise ValueError("query parameter 'days' must be an integer") from e
        return datetime.now(UTC) - timedelta(days=days)


def main() -> None:
    """
    Runs the web backend as a development server on the interface and port given on the command line.
    """
    # parse the command line
    parser = argparse.ArgumentParser(description="Runs the Penpot error report analysis dashboard.")
    parser.add_argument("--host", default="127.0.0.1", help="the interface to bind to")
    parser.add_argument("--port", type=int, default=5100, help="the port to listen on")
    args = parser.parse_args()

    # run the server
    configure_logging()
    backend = WebBackend(AnalysisContext.create_default())
    backend.flask_app.run(host=args.host, port=args.port)
