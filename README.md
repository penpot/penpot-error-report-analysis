# Penpot Error Report Analysis

A platform for the systematic, LLM-driven analysis of Penpot error reports.

Error reports (retrieved from the Penpot RPC API) are grouped into equivalence classes via a
versioned fingerprint algorithm. An LLM, connected through the bundled MCP server, analyzes
representative members of unanalyzed classes and stores markdown insights in a local database.
Humans review the results through a web dashboard.

## Components

* `src/error_analysis/reports` — report source client and typed report model
* `src/error_analysis/fingerprint` — versioned fingerprint algorithms
* `src/error_analysis/persistence` — SQLAlchemy model and repository abstractions
* `src/error_analysis/classify` — classification workflow
* `src/error_analysis/mcp` — MCP server exposing the analysis workflow to LLMs
* `src/error_analysis/web` — Flask backend and jQuery dashboard

## Setup

Requires [pixi](https://pixi.sh). Install the environment with:

    pixi install

Configuration is read from environment variables or local `.env` file
(`PENPOT_API_URI`, `PENPOT_ACCESS_TOKEN` with `error-reports:read` permission).

## Usage

### MCP server (LLM interface)

Run the server with HTTP transport and register the corresponding URL (`http://127.0.0.1:5101/mcp`) 
in your client:

    pixi run error-analysis-mcp --transport streamable-http [--host 127.0.0.1] [--port 5101]

The server can alternatively be run with stdio transport:

    pixi run --manifest-path /path/to/penpot-error-report-analysis/pyproject.toml error-analysis-mcp

An analysis session is started by calling the `bootstrap_analysis` tool.
Instruct the agent to call it, e.g. as follows:

> *Start Penpot error analysis.*

This classifies recent reports and returns an overview of the top unanalyzed classes, 
which the LLM presents to the user, who decides how many (or which) classes shall be analyzed. 

The chosen classes are then retrieved in full detail via `get_analysis_candidates`, 
which also delivers the analysis workflow instructions. 

Beyond analysis, the classes themselves can be curated through the MCP server: `set_class_triage`
records a class's severity, the numbers of the GitHub issue and the pull request addressing it, and
the *fixed* marker (only the given properties change).

> [!IMPORTANT]
> The analyzing LLM should additionally have access to the 
> [*agentic Penpot development environment*](https://help.penpot.app/technical-guide/developer/agentic-devenv/) 
> for root-cause investigation.  
> The git revision should ideally match the Penpot deployment that produced the reports.

### Classification backfills

Reports are classified incrementally (each report's details are retrieved once). For large windows
over high-volume deployments, perform the initial classification via the command line rather than
through the MCP tool:

    pixi run error-analysis-classify --days 30 [--concurrency 8]

Subsequent `bootstrap_analysis` calls then only classify reports that arrived since.

### Web dashboard

    pixi run error-analysis-web [--host 127.0.0.1] [--port 5100]

serves the dashboard at http://127.0.0.1:5100. The dashboard lists the equivalence classes of a
selectable time window, optionally restricted to a single deployment version, and provides per-class
fingerprint signatures, analysis insights, and member report inspection.

Each class carries a triage state that is curated in the dashboard: the assessed severity (shown as
a column of the class list), the numbers of the GitHub issue and the pull request addressing it, and
a *fixed* marker for defects that are resolved but keep appearing in reports of older windows. The
severity is equally assigned by the analyzing LLM via the `store_insight` and `set_class_triage` tools.

## Development

    pixi run check   # format, lint, typecheck

See `CONVENTIONS.md` for binding code style and design conventions.
