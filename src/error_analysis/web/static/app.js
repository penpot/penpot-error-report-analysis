/**
 * Penpot error report analysis dashboard.
 *
 * Object-oriented structure: ApiClient (server communication), ClassListView (left pane),
 * ClassDetailView (right pane), ReportOverlay (member report inspection), App (wiring).
 */
"use strict";

/**
 * Communicates with the JSON API of the analysis backend.
 *
 * Endpoint URLs are relative to the document's directory, so the application works regardless of
 * the path it is mounted at (e.g. behind a reverse proxy serving it under a sub-path).
 */
class ApiClient {
    /**
     * @param {number} days the time window in days
     * @param {?string} version the deployment version to restrict the counts to; null for all versions
     * @returns {Promise<Array>} the class overviews
     */
    listClasses(days, version) {
        return $.getJSON("api/classes", version ? { days: days, version: version } : { days: days });
    }

    /**
     * @param {number} days the time window in days
     * @returns {Promise<Array<string>>} the deployment versions the window's reports originate from
     */
    listVersions(days) {
        return $.getJSON("api/versions", { days: days });
    }

    /**
     * @param {number} classId the id of the equivalence class
     * @returns {Promise<Object>} the class details (class, insights, members)
     */
    getClass(classId) {
        return $.getJSON(`api/classes/${classId}`);
    }

    /**
     * @param {string} reportId the id of the error report
     * @returns {Promise<Object>} the report details
     */
    getReport(reportId) {
        return $.getJSON(`api/reports/${reportId}`);
    }

    /**
     * @param {number} classId the id of the equivalence class
     * @param {number} insightId the id of the insight to file as an issue
     * @returns {Promise<Object>} the issue draft (title, body, prefill_url, new_issue_url)
     */
    getIssueDraft(classId, insightId) {
        return $.getJSON(`api/classes/${classId}/insights/${insightId}/issue-draft`);
    }

    /**
     * @param {number} classId the id of the equivalence class
     * @param {?number} issueNumber the number of the filed GitHub issue; null to remove it
     * @returns {Promise<Object>} the updated equivalence class
     */
    setIssueNumber(classId, issueNumber) {
        return $.ajax({
            url: `api/classes/${classId}/issue`,
            method: "PUT",
            contentType: "application/json",
            data: JSON.stringify({ issue_number: issueNumber }),
            dataType: "json",
        });
    }
}

/** Copies text to the clipboard, falling back to a selection-based copy where unavailable. */
class Clipboard {
    /**
     * @param {string} text the text to copy
     * @returns {Promise<void>} resolved once the text has been copied
     */
    static copy(text) {
        if (navigator.clipboard && window.isSecureContext) {
            return navigator.clipboard.writeText(text);
        }
        return new Promise((resolve, reject) => {
            const $area = $("<textarea>").val(text).css({ position: "fixed", opacity: 0 }).appendTo("body");
            $area.get(0).select();
            const succeeded = document.execCommand("copy");
            $area.remove();
            succeeded ? resolve() : reject(new Error("copy failed"));
        });
    }
}

/** Renders the table of equivalence classes and reports row selection. */
class ClassListView {
    /**
     * @param {jQuery} $tbody the table body to render rows into
     * @param {jQuery} $emptyNote the note shown when no classes exist
     * @param {jQuery} $countLabel the label showing the class count
     * @param {function(number): void} onSelect invoked with the class id when a row is selected
     */
    constructor($tbody, $emptyNote, $countLabel, onSelect) {
        this._$tbody = $tbody;
        this._$emptyNote = $emptyNote;
        this._$countLabel = $countLabel;
        this._onSelect = onSelect;
    }

    /**
     * Renders the given class overviews.
     * @param {Array} overviews the class overviews, ordered by report count descending
     */
    render(overviews) {
        this._$tbody.empty();
        this._$emptyNote.toggleClass("hidden", overviews.length > 0);
        this._$countLabel.text(`${overviews.length} classes`);
        overviews.forEach((overview) => this._$tbody.append(this._buildRow(overview)));
    }

    /**
     * Highlights the row of the given class as selected.
     * @param {number} classId the id of the selected class
     */
    markSelected(classId) {
        this._$tbody.find("tr").removeClass("selected");
        this._$tbody.find(`tr[data-class-id='${classId}']`).addClass("selected");
    }

    /**
     * @param {Object} overview the class overview to build a row for
     * @returns {jQuery} the table row
     */
    _buildRow(overview) {
        const analyzed = overview.insight_count > 0;
        const $status = $("<span>")
            .addClass("badge")
            .addClass(analyzed ? "done" : "pending")
            .text(analyzed ? `${overview.insight_count} insight${overview.insight_count > 1 ? "s" : ""}` : "pending");
        const $row = $("<tr>")
            .attr("data-class-id", overview.id)
            .append($("<td>").addClass("col-id").text(overview.id))
            .append($("<td>").addClass("hint-cell").text(overview.exemplar_hint || "(no hint)"))
            .append($("<td>").addClass("col-num").text(overview.report_count))
            .append($("<td>").addClass("col-date").text(Formats.date(overview.last_seen_at)))
            .append($("<td>").addClass("col-status").append($status));
        $row.on("click", () => this._onSelect(overview.id));
        return $row;
    }
}

/** Renders the details of a selected equivalence class. */
class ClassDetailView {
    /**
     * @param {jQuery} $pane the pane to render into
     * @param {ApiClient} api the client used to load issue drafts and record issue numbers
     * @param {function(string): void} onReportOpen invoked with the report id when a member is opened
     * @param {function(): void} onClassChanged invoked when the displayed class was modified
     */
    constructor($pane, api, onReportOpen, onClassChanged) {
        this._$pane = $pane;
        this._api = api;
        this._onReportOpen = onReportOpen;
        this._onClassChanged = onClassChanged;
    }

    /**
     * Renders the given class details.
     * @param {Object} detail the class details (class fields, insights, members)
     */
    render(detail) {
        this._$pane.empty();
        this._$pane
            .append(this._buildHeader(detail))
            .append(this._buildInsightsSection(detail.insights))
            .append(this._buildSection("Fingerprint signature", $("<pre>").addClass("code-block").text(detail.signature)))
            .append(this._buildMembersSection(detail.members));
    }

    /**
     * @param {Object} detail the class details
     * @returns {jQuery} the header element (hint title and metadata line)
     */
    _buildHeader(detail) {
        const $meta = $("<div>").addClass("detail-meta")
            .append($("<span>").text(`class #${detail.id}`))
            .append($("<span>").text(`first seen ${Formats.date(detail.first_seen_at)}`))
            .append($("<span>").text(`last seen ${Formats.date(detail.last_seen_at)}`))
            .append($("<span>").text(`fingerprint v${detail.algorithm_version}`))
            .append($("<span>").attr("title", detail.digest).text(`digest ${detail.digest.substring(0, 12)}…`));
        return $("<div>").addClass("detail-header")
            .append($("<h2>").text(detail.exemplar_hint || "(no hint)"))
            .append($meta)
            .append(this._buildIssueTracker(detail));
    }

    /**
     * @param {Object} detail the class details
     * @returns {jQuery} the control displaying or capturing the number of the filed GitHub issue
     */
    _buildIssueTracker(detail) {
        const $tracker = $("<div>").addClass("issue-tracker");
        if (detail.issue_number) {
            const $link = $("<a>")
                .attr("href", `https://github.com/penpot/penpot/issues/${detail.issue_number}`)
                .attr("target", "_blank")
                .attr("rel", "noopener")
                .text(`Issue #${detail.issue_number}`);
            const $remove = $("<button>").attr("type", "button").text("Unlink")
                .on("click", () => this._storeIssueNumber(detail.id, null));
            return $tracker.addClass("filed").append($("<span>").addClass("badge done").text("filed")).append($link).append($remove);
        }
        const $input = $("<input>").attr({ type: "number", min: "1", placeholder: "issue #" });
        const $save = $("<button>").attr("type", "button").text("Link issue")
            .on("click", () => {
                const value = parseInt($input.val(), 10);
                if (value > 0) this._storeIssueNumber(detail.id, value);
            });
        return $tracker.append($("<span>").addClass("muted").text("No issue linked")).append($input).append($save);
    }

    /**
     * Records the given issue number for the given class and refreshes the display.
     * @param {number} classId the id of the equivalence class
     * @param {?number} issueNumber the issue number, or null to remove it
     */
    _storeIssueNumber(classId, issueNumber) {
        this._api.setIssueNumber(classId, issueNumber).then(() => this._onClassChanged());
    }

    /**
     * @param {Array} insights the insights of the class
     * @returns {jQuery} the insights section
     */
    _buildInsightsSection(insights) {
        if (insights.length === 0) {
            const $note = $("<p>").addClass("no-insights")
                .text("Not yet analyzed. Run an analysis session via the MCP server to gather insights.");
            return this._buildSection("Insights", $note);
        }
        const $insights = insights.map((insight) => this._buildInsight(insight));
        return this._buildSection("Insights", $insights);
    }

    /**
     * @param {Object} insight the insight to render
     * @returns {jQuery} the insight card, with markdown content rendered to HTML
     */
    _buildInsight(insight) {
        const author = insight.author ? ` by ${insight.author}` : "";
        const $reportLink = $("<a>")
            .attr("href", "#")
            .text(insight.analyzed_report_id)
            .on("click", (event) => {
                event.preventDefault();
                this._onReportOpen(insight.analyzed_report_id);
            });
        const $meta = $("<p>").addClass("insight-meta")
            .text(`${Formats.dateTime(insight.created_at)}${author} — analyzed report `)
            .append($reportLink);
        return $("<article>").addClass("insight")
            .append($("<div>").addClass("insight-content").html(marked.parse(insight.markdown)))
            .append($meta)
            .append(this._buildIssueActions(insight));
    }

    /**
     * @param {Object} insight the insight the actions pertain to
     * @returns {jQuery} the actions for filing the insight as a GitHub issue
     */
    _buildIssueActions(insight) {
        const $status = $("<span>").addClass("muted issue-action-status");
        const $create = $("<button>").attr("type", "button").text("Create GitHub issue (title only)")
            .on("click", () => this._openIssueForm(insight, $status));
        const $copy = $("<button>").attr("type", "button").text("Copy issue body")
            .on("click", () => this._copyIssueBody(insight, $status));
        return $("<div>").addClass("issue-actions").append($create).append($copy).append($status);
    }

    /**
     * Opens GitHub's issue form for the given insight with the title prefilled, the body having been
     * copied to the clipboard for insertion into the form.
     * @param {Object} insight the insight to file
     * @param {jQuery} $status the element conveying the outcome
     */
    _openIssueForm(insight, $status) {
        this._api.getIssueDraft(insight.class_id, insight.id)
            .then((draft) => Clipboard.copy(draft.body).then(() => draft))
            .then((draft) => {
                window.open(draft.form_url, "_blank", "noopener");
                $status.text("Issue form opened with the title filled in; paste the body (copied to the clipboard) and submit.");
            })
            .catch(() => $status.text("Preparing the issue failed."));
    }

    /**
     * Copies the issue body of the given insight to the clipboard.
     * @param {Object} insight the insight to copy the issue body of
     * @param {jQuery} $status the element conveying the outcome
     */
    _copyIssueBody(insight, $status) {
        this._api.getIssueDraft(insight.class_id, insight.id)
            .then((draft) => Clipboard.copy(draft.body))
            .then(() => $status.text("Issue body copied to the clipboard."))
            .catch(() => $status.text("Copying failed."));
    }

    /**
     * @param {Array} members the member reports of the class
     * @returns {jQuery} the members section
     */
    _buildMembersSection(members) {
        const $list = $("<ul>").addClass("member-list");
        members.forEach((member) => {
            const $link = $("<a>")
                .attr("href", "#")
                .text(member.report_id)
                .on("click", (event) => {
                    event.preventDefault();
                    this._onReportOpen(member.report_id);
                });
            $list.append(
                $("<li>")
                    .append($("<span>").addClass("member-date").text(Formats.dateTime(member.created_at)))
                    .append($link)
            );
        });
        return this._buildSection(`Member reports (${members.length} most recent)`, $list);
    }

    /**
     * @param {string} title the section title
     * @param {jQuery|Array} $content the section content
     * @returns {jQuery} the section element
     */
    _buildSection(title, $content) {
        return $("<section>").addClass("detail-section").append($("<h3>").text(title)).append($content);
    }
}

/** Shows the details of a single error report in an overlay. */
class ReportOverlay {
    /**
     * @param {jQuery} $overlay the overlay root element
     */
    constructor($overlay) {
        this._$overlay = $overlay;
        this._$title = $overlay.find("#report-title");
        this._$body = $overlay.find("#report-body");
        $overlay.find("#report-close").on("click", () => this.hide());
        $overlay.on("click", (event) => {
            if (event.target === $overlay.get(0)) this.hide();
        });
        $(document).on("keydown", (event) => {
            if (event.key === "Escape") this.hide();
        });
    }

    /**
     * Shows the given report.
     * @param {Object} report the report details
     */
    show(report) {
        this._$title.text(`${report.shape} report ${report.id}`);
        this._$body.empty()
            .append(this._section("Created", $("<p>").text(Formats.dateTime(report.created_at))))
            .append(this._section("Hint", $("<pre>").addClass("code-block").text(report.hint || "(none)")));
        this._appendShapeSections(report);
        this._appendOptionalCode("Context", report.context_edn);
        this._$overlay.removeClass("hidden");
    }

    /** Hides the overlay. */
    hide() {
        this._$overlay.addClass("hidden");
    }

    /**
     * Appends the sections specific to the report shape.
     * @param {Object} report the report details
     */
    _appendShapeSections(report) {
        if (report.shape === "backend") {
            this._appendOptionalCode("Trace", report.trace);
            this._appendOptionalCode("Props", report.props_edn);
            this._appendOptionalCode("Params", report.params_edn);
        } else {
            this._appendOptionalCode("Error data", report.data_section);
            this._appendOptionalCode("Trace", report.trace_section);
            this._appendOptionalCode("Location", report.href);
        }
    }

    /**
     * Appends a code section if the given content is present.
     * @param {string} title the section title
     * @param {?string} content the section content
     */
    _appendOptionalCode(title, content) {
        if (content) {
            this._$body.append(this._section(title, $("<pre>").addClass("code-block").text(content)));
        }
    }

    /**
     * @param {string} title the section title
     * @param {jQuery} $content the section content
     * @returns {jQuery} the section element
     */
    _section(title, $content) {
        return $("<section>").addClass("detail-section").append($("<h3>").text(title)).append($content);
    }
}

/** Formatting helpers for instants. */
class Formats {
    /**
     * @param {string} isoInstant an ISO-8601 instant
     * @returns {string} the instant's date part (YYYY-MM-DD)
     */
    static date(isoInstant) {
        return isoInstant.substring(0, 10);
    }

    /**
     * @param {string} isoInstant an ISO-8601 instant
     * @returns {string} the instant reduced to minute precision (YYYY-MM-DD HH:MM)
     */
    static dateTime(isoInstant) {
        return isoInstant.substring(0, 16).replace("T", " ");
    }
}

/** The selector of the deployment version the class list is restricted to. */
class VersionFilter {
    /**
     * @param {jQuery} $select the selector element
     * @param {ApiClient} api the client used to load the available versions
     */
    constructor($select, api) {
        this._$select = $select;
        this._api = api;
    }

    /** @returns {?string} the selected version, or null if all versions are shown */
    selected() {
        return this._$select.val() || null;
    }

    /**
     * Loads the versions of the given time window into the selector, retaining the current selection
     * if the window still contains it.
     * @param {number} days the time window in days
     * @returns {Promise<void>} resolved once the selector has been populated
     */
    load(days) {
        const previous = this.selected();
        return this._api.listVersions(days).then((versions) => {
            this._$select.empty().append($("<option>").attr("value", "").text("All versions"));
            versions.forEach((version) => this._$select.append($("<option>").attr("value", version).text(version)));
            if (previous && versions.includes(previous)) this._$select.val(previous);
        });
    }
}

/** Wires the views together and drives data loading. */
class App {
    constructor() {
        this._api = new ApiClient();
        this._overlay = new ReportOverlay($("#report-overlay"));
        this._listView = new ClassListView($("#class-rows"), $("#list-empty"), $("#class-count"), (classId) => this._selectClass(classId));
        this._detailView = new ClassDetailView(
            $("#detail-pane"),
            this._api,
            (reportId) => this._openReport(reportId),
            () => this._refreshSelectedClass()
        );
        this._$windowDays = $("#window-days");
        this._versionFilter = new VersionFilter($("#version-filter"), this._api);
        this._selectedClassId = null;
    }

    /** Starts the application. */
    start() {
        this._$windowDays.on("change", () => this._loadWindow());
        $("#version-filter").on("change", () => this._loadClasses());
        this._loadWindow();
    }

    /** Loads the versions and classes of the selected time window. */
    _loadWindow() {
        this._versionFilter.load(this._days()).always(() => this._loadClasses());
    }

    /** Loads and renders the class list for the selected time window and version. */
    _loadClasses() {
        this._api.listClasses(this._days(), this._versionFilter.selected())
            .then((overviews) => {
                this._listView.render(overviews);
                if (this._selectedClassId !== null) this._listView.markSelected(this._selectedClassId);
            })
            .catch(() => this._listView.render([]));
    }

    /** @returns {number} the selected time window in days */
    _days() {
        return Number(this._$windowDays.val());
    }

    /**
     * Loads and renders the details of the given class.
     * @param {number} classId the id of the selected class
     */
    _selectClass(classId) {
        this._selectedClassId = classId;
        this._listView.markSelected(classId);
        this._api.getClass(classId).then((detail) => this._detailView.render(detail));
    }

    /** Reloads the currently selected class, reflecting modifications made to it. */
    _refreshSelectedClass() {
        if (this._selectedClassId !== null) {
            this._selectClass(this._selectedClassId);
        }
    }

    /**
     * Loads and shows the given report in the overlay.
     * @param {string} reportId the id of the report to open
     */
    _openReport(reportId) {
        this._api.getReport(reportId).then((report) => this._overlay.show(report));
    }
}

$(() => new App().start());
