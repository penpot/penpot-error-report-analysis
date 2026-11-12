/**
 * Penpot error report analysis dashboard.
 *
 * Object-oriented structure: ApiClient (server communication), ClassListView (left pane),
 * ClassDetailView (right pane) with TriagePanel and InsightComposer, ReportOverlay (member report
 * inspection), App (wiring).
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
     * @returns {Promise<Object>} the class details (class, triage, insights, members)
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
     * @returns {Promise<Object>} the issue draft (title, body, form_url)
     */
    getIssueDraft(classId, insightId) {
        return $.getJSON(`api/classes/${classId}/insights/${insightId}/issue-draft`);
    }

    /**
     * @param {number} classId the id of the equivalence class
     * @param {Object} triage the complete triage state (severity, issue_number, pr_number, fixed)
     * @returns {Promise<Object>} the updated equivalence class
     */
    setTriage(classId, triage) {
        return this._send(`api/classes/${classId}/triage`, "PUT", triage);
    }

    /**
     * @param {number} classId the id of the equivalence class
     * @param {Object} insight the insight to store (markdown, author, analyzed_report_id)
     * @returns {Promise<Object>} the stored insight
     */
    addInsight(classId, insight) {
        return this._send(`api/classes/${classId}/insights`, "POST", insight);
    }

    /**
     * @param {string} url the endpoint URL
     * @param {string} method the HTTP method
     * @param {Object} payload the request body
     * @returns {Promise<Object>} the decoded response
     */
    _send(url, method, payload) {
        return $.ajax({
            url: url,
            method: method,
            contentType: "application/json",
            data: JSON.stringify(payload),
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

/** The severities a class can be assigned, and the badges conveying triage state. */
class Triage {
    /** @returns {Array<string>} the severity values, most severe first */
    static severities() {
        return ["critical", "high", "medium", "low"];
    }

    /**
     * @param {?string} severity the assigned severity, if any
     * @returns {jQuery} the badge conveying the severity, or a placeholder if none is assigned
     */
    static severityBadge(severity) {
        if (!severity) return $("<span>").addClass("muted").text("—");
        return $("<span>").addClass(`badge severity-${severity}`).text(severity);
    }

    /**
     * @param {Object} overview the class overview
     * @returns {Array<jQuery>} the badges conveying the analysis and resolution state of the class
     */
    static statusBadges(overview) {
        const triage = overview.triage;
        const badges = [];
        if (overview.insight_count > 0) {
            badges.push($("<span>").addClass("badge done").text(`${overview.insight_count} insight${overview.insight_count > 1 ? "s" : ""}`));
        } else {
            badges.push($("<span>").addClass("badge pending").text("pending"));
        }
        if (triage.issue_number) badges.push($("<span>").addClass("badge issue").text(`#${triage.issue_number}`));
        if (triage.pr_number) badges.push($("<span>").addClass("badge pr").text(`PR #${triage.pr_number}`));
        if (triage.fixed) badges.push($("<span>").addClass("badge fixed").text("fixed"));
        return badges;
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
        const $row = $("<tr>")
            .attr("data-class-id", overview.id)
            .toggleClass("resolved", overview.triage.fixed)
            .append($("<td>").addClass("col-id").text(overview.id))
            .append($("<td>").addClass("hint-cell").text(overview.exemplar_hint || "(no hint)"))
            .append($("<td>").addClass("col-num").text(overview.report_count))
            .append($("<td>").addClass("col-severity").append(Triage.severityBadge(overview.triage.severity)))
            .append($("<td>").addClass("col-date").text(Formats.date(overview.last_seen_at)))
            .append($("<td>").addClass("col-status").append(Triage.statusBadges(overview)));
        $row.on("click", () => this._onSelect(overview.id));
        return $row;
    }
}

/**
 * Renders the triage controls of a class: the assessed severity, the linked GitHub issue and pull
 * request, and the fixed marker.
 *
 * Each control edit submits the complete triage state, which the panel holds for the displayed class.
 */
class TriagePanel {
    /**
     * @param {ApiClient} api the client used to record the triage state
     * @param {function(): void} onChanged invoked once a modification has been recorded
     */
    constructor(api, onChanged) {
        this._api = api;
        this._onChanged = onChanged;
        this._classId = null;
        this._triage = null;
    }

    /**
     * @param {Object} detail the class details
     * @returns {jQuery} the triage panel
     */
    render(detail) {
        this._classId = detail.id;
        this._triage = detail.triage;
        return $("<div>").addClass("triage-panel")
            .append(this._buildSeverityControl())
            .append(this._buildNumberControl("Issue", "issue_number", "issues"))
            .append(this._buildNumberControl("PR", "pr_number", "pull"))
            .append(this._buildFixedControl());
    }

    /** @returns {jQuery} the severity selector */
    _buildSeverityControl() {
        const $select = $("<select>").append($("<option>").attr("value", "").text("unassessed"));
        Triage.severities().forEach((severity) => $select.append($("<option>").attr("value", severity).text(severity)));
        $select.val(this._triage.severity || "");
        $select.on("change", () => this._store({ severity: $select.val() || null }));
        return this._control("Severity", $select);
    }

    /**
     * @param {string} label the label of the control
     * @param {string} field the triage field holding the number
     * @param {string} urlSegment the GitHub URL segment the number is linked under
     * @returns {jQuery} the control displaying the linked number, or capturing one if none is linked
     */
    _buildNumberControl(label, field, urlSegment) {
        const number = this._triage[field];
        if (number) {
            const $link = $("<a>")
                .attr({ href: `https://github.com/penpot/penpot/${urlSegment}/${number}`, target: "_blank", rel: "noopener" })
                .text(`#${number}`);
            const $unlink = $("<button>").attr("type", "button").addClass("link-button").attr("title", `Unlink ${label.toLowerCase()}`)
                .text("×")
                .on("click", () => this._store({ [field]: null }));
            return this._control(label, $link, $unlink);
        }
        const $input = $("<input>").attr({ type: "number", min: "1", placeholder: "#" });
        const submit = () => {
            const value = parseInt($input.val(), 10);
            if (value > 0) this._store({ [field]: value });
        };
        $input.on("keydown", (event) => {
            if (event.key === "Enter") submit();
        });
        return this._control(label, $input, $("<button>").attr("type", "button").text("Link").on("click", submit));
    }

    /** @returns {jQuery} the checkbox marking the underlying defect as fixed */
    _buildFixedControl() {
        const $checkbox = $("<input>").attr("type", "checkbox").prop("checked", this._triage.fixed)
            .on("change", () => this._store({ fixed: $checkbox.prop("checked") }));
        const $label = $("<label>").addClass("fixed-control")
            .append($checkbox)
            .append($("<span>").text("Fixed"))
            .attr("title", "Mark as fixed; reports of older windows keep showing up");
        return $("<div>").addClass("triage-control").append($label);
    }

    /**
     * @param {string} label the label of the control
     * @param {...jQuery} $parts the elements forming the control
     * @returns {jQuery} the labeled control
     */
    _control(label, ...$parts) {
        return $("<div>").addClass("triage-control").append($("<span>").addClass("triage-label").text(label)).append($parts);
    }

    /**
     * Records the triage state with the given fields replaced.
     * @param {Object} changes the triage fields to change
     */
    _store(changes) {
        this._api.setTriage(this._classId, Object.assign({}, this._triage, changes)).then(() => this._onChanged());
    }
}

/** Captures an insight written by hand and stores it for an equivalence class. */
class InsightComposer {
    /** the storage key under which the author is remembered across sessions */
    static AUTHOR_KEY = "error-analysis.insight-author";

    /**
     * @param {ApiClient} api the client used to store the insight
     * @param {function(): void} onStored invoked once an insight has been stored
     */
    constructor(api, onStored) {
        this._api = api;
        this._onStored = onStored;
    }

    /**
     * @param {Object} detail the class details the insight is written for
     * @returns {jQuery} the composer: a button revealing the entry form
     */
    render(detail) {
        const $form = this._buildForm(detail).addClass("hidden");
        const $toggle = $("<button>").attr("type", "button").addClass("compose-toggle").text("Write insight")
            .on("click", () => {
                $form.toggleClass("hidden");
                if (!$form.hasClass("hidden")) $form.find("textarea").trigger("focus");
            });
        return $("<div>").addClass("insight-composer").append($toggle).append($form);
    }

    /**
     * @param {Object} detail the class details the insight is written for
     * @returns {jQuery} the entry form
     */
    _buildForm(detail) {
        const $markdown = $("<textarea>").attr({ rows: 12, placeholder: "Insight in markdown — what is known about this error class?" });
        const $author = $("<input>").attr({ type: "text", placeholder: "author" }).val(this._rememberedAuthor());
        const $report = this._buildReportSelect(detail.members);
        const $status = $("<span>").addClass("muted compose-status");
        const $save = $("<button>").attr("type", "button").text("Save insight")
            .on("click", () => this._store(detail.id, $markdown, $author, $report, $status));
        const $fields = $("<div>").addClass("compose-fields")
            .append($("<label>").text("Author").append($author))
            .append($("<label>").text("Analyzed report").append($report))
            .append($save)
            .append($status);
        return $("<div>").addClass("compose-form").append($markdown).append($fields);
    }

    /**
     * @param {Array} members the member reports of the class
     * @returns {jQuery} the selector of the report the insight refers to
     */
    _buildReportSelect(members) {
        const $select = $("<select>");
        members.forEach((member) => {
            $select.append($("<option>").attr("value", member.report_id).text(`${Formats.dateTime(member.created_at)} · ${member.report_id}`));
        });
        return $select;
    }

    /**
     * Stores the composed insight.
     * @param {number} classId the id of the equivalence class
     * @param {jQuery} $markdown the markdown input
     * @param {jQuery} $author the author input
     * @param {jQuery} $report the analyzed report selector
     * @param {jQuery} $status the element conveying the outcome
     */
    _store(classId, $markdown, $author, $report, $status) {
        const markdown = $markdown.val().trim();
        if (!markdown) {
            $status.text("The insight must not be empty.");
            return;
        }
        const author = $author.val().trim();
        this._rememberAuthor(author);
        this._api.addInsight(classId, { markdown: markdown, author: author, analyzed_report_id: $report.val() })
            .then(() => this._onStored())
            .catch(() => $status.text("Storing the insight failed."));
    }

    /** @returns {string} the author remembered from the previous entry, if any */
    _rememberedAuthor() {
        try {
            return window.localStorage.getItem(InsightComposer.AUTHOR_KEY) || "";
        } catch (e) {
            return "";
        }
    }

    /**
     * Remembers the author for subsequent entries.
     * @param {string} author the author to remember
     */
    _rememberAuthor(author) {
        try {
            window.localStorage.setItem(InsightComposer.AUTHOR_KEY, author);
        } catch (e) {
            // storage being unavailable merely costs the convenience of a prefilled author
        }
    }
}

/** Renders the details of a selected equivalence class. */
class ClassDetailView {
    /**
     * @param {jQuery} $pane the pane to render into
     * @param {ApiClient} api the client used to load issue drafts and record modifications
     * @param {function(string): void} onReportOpen invoked with the report id when a member is opened
     * @param {function(): void} onClassChanged invoked when the displayed class was modified
     */
    constructor($pane, api, onReportOpen, onClassChanged) {
        this._$pane = $pane;
        this._api = api;
        this._onReportOpen = onReportOpen;
        this._triagePanel = new TriagePanel(api, onClassChanged);
        this._composer = new InsightComposer(api, onClassChanged);
    }

    /**
     * Renders the given class details.
     * @param {Object} detail the class details (class fields, triage, insights, members)
     */
    render(detail) {
        this._$pane.empty();
        this._$pane
            .append(this._buildHeader(detail))
            .append(this._buildInsightsSection(detail))
            .append(this._buildSection("Fingerprint signature", $("<pre>").addClass("code-block").text(detail.signature)))
            .append(this._buildMembersSection(detail.members));
    }

    /**
     * @param {Object} detail the class details
     * @returns {jQuery} the header element (hint title, metadata line, and triage panel)
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
            .append(this._triagePanel.render(detail));
    }

    /**
     * @param {Object} detail the class details
     * @returns {jQuery} the insights section, including the composer for manually written insights
     */
    _buildInsightsSection(detail) {
        const $content = [];
        if (detail.insights.length === 0) {
            $content.push($("<p>").addClass("no-insights")
                .text("Not yet analyzed. Run an analysis session via the MCP server, or write an insight yourself."));
        } else {
            detail.insights.forEach((insight) => $content.push(this._buildInsight(insight)));
        }
        $content.push(this._composer.render(detail));
        return this._buildSection("Insights", $content);
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

    /** Reloads the currently selected class and the list, reflecting modifications made to the class. */
    _refreshSelectedClass() {
        if (this._selectedClassId !== null) {
            this._selectClass(this._selectedClassId);
            this._loadClasses();
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
