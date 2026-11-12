"""The MCP server exposing the analysis platform to LLMs."""

from __future__ import annotations

import argparse
import logging
import os
import signal
from types import FrameType
from typing import Any

import docstring_parser
from mcp.server.fastmcp.exceptions import ToolError
from mcp.server.fastmcp.server import FastMCP
from mcp.server.fastmcp.tools.base import Tool as FastMCPTool
from mcp.types import ToolAnnotations

from error_analysis.context import AnalysisContext
from error_analysis.logging_config import configure_logging
from error_analysis.mcp.tools import (
    BootstrapAnalysisTool,
    GetAnalysisCandidatesTool,
    GetEquivalenceClassTool,
    GetReportDetailsTool,
    ListClassOverviewsTool,
    SetClassTriageTool,
    StoreInsightTool,
)
from error_analysis.mcp.tools_base import Tool, ToolCallError

log = logging.getLogger(__name__)

_SERVER_INSTRUCTIONS = """\
This server provides access to the Penpot error report analysis platform. Error reports are grouped
into equivalence classes (one class per underlying issue); the platform stores class associations and
analysis insights. To perform an analysis session, call bootstrap_analysis once and follow the
instructions it returns: it provides an overview of the unanalyzed classes, upon which the user decides
what shall be analyzed; the chosen classes are then retrieved via get_analysis_candidates.
Beyond analysis, the triage state of the classes (severity, GitHub issue and pull request numbers,
fixed marker) is recorded via set_class_triage."""

_TOOL_CLASSES: list[type[Tool]] = [
    BootstrapAnalysisTool,
    GetAnalysisCandidatesTool,
    ListClassOverviewsTool,
    GetEquivalenceClassTool,
    GetReportDetailsTool,
    StoreInsightTool,
    SetClassTriageTool,
]


class McpServerFactory:
    """
    Creates the MCP server, converting the platform's tools into FastMCP tools.

    Tool descriptions and parameter documentation are derived from the tools' ``apply`` docstrings,
    following the approach of Serena's MCP integration.
    """

    def __init__(self, context: AnalysisContext) -> None:
        """
        :param context: the platform services the served tools operate on
        """
        self._context = context

    def create_server(self, host: str = "127.0.0.1", port: int = 5101) -> FastMCP:
        """
        :param host: the interface to bind to (relevant for HTTP transport only)
        :param port: the port to listen on (relevant for HTTP transport only)
        :return: the fully configured MCP server
        """
        mcp = FastMCP(name="penpot-error-analysis", instructions=_SERVER_INSTRUCTIONS, host=host, port=port)

        # register the converted platform tools, bypassing FastMCP's function-based registration
        tool_manager_tools = mcp._tool_manager._tools  # noqa: SLF001 (established registration approach, cf. Serena)
        for tool_class in _TOOL_CLASSES:
            tool = tool_class(self._context)
            tool_manager_tools[tool.get_name()] = self._make_mcp_tool(tool)
        log.info("MCP server configured with %d tools: %s", len(tool_manager_tools), list(tool_manager_tools))

        return mcp

    @staticmethod
    def _make_mcp_tool(tool: Tool) -> FastMCPTool:
        """
        Converts the given platform tool into a FastMCP tool, deriving the description and parameter
        documentation from the ``apply`` docstring.

        :param tool: the tool to convert
        :return: the FastMCP tool
        """
        # derive the schema from the apply method's signature
        metadata = tool.get_apply_fn_metadata()
        parameters = metadata.arg_model.model_json_schema()

        # derive the tool description from the docstring's description and return documentation
        docstring = docstring_parser.parse(tool.get_apply_docstring())
        description = (docstring.description or "").strip().rstrip(".")
        if description:
            description += "."
        if docstring.returns is not None and docstring.returns.description:
            description += f" Returns {docstring.returns.description.strip().rstrip('.')}."

        # attach the documented parameter descriptions to the schema
        param_docs = {param.arg_name: param.description for param in docstring.params}
        for name, properties in parameters.get("properties", {}).items():
            if (param_doc := param_docs.get(name)) is not None:
                properties["description"] = param_doc.strip().rstrip(".") + "."

        # wrap the tool application, translating tool call errors into the MCP error type
        def execute(**kwargs: Any) -> str:
            try:
                return tool.apply_ex(**kwargs)
            except ToolCallError as e:
                raise ToolError(str(e)) from e

        title = " ".join(word.capitalize() for word in tool.get_name().split("_"))
        return FastMCPTool(
            fn=execute,
            name=tool.get_name(),
            title=title,
            description=description,
            parameters=parameters,
            fn_metadata=metadata,
            is_async=False,
            context_kwarg=None,
            annotations=ToolAnnotations(title=title, readOnlyHint=False, destructiveHint=False),
        )


def main() -> None:
    """
    Runs the MCP server on the transport selected via the command line: ``stdio`` (default; for
    clients spawning the server locally) or ``streamable-http`` (for clients connecting via URL,
    served at ``http://<host>:<port>/mcp``).
    """
    # parse the command line
    parser = argparse.ArgumentParser(description="Runs the Penpot error report analysis MCP server.")
    parser.add_argument("--transport", choices=["stdio", "streamable-http"], default="stdio", help="the MCP transport to serve on")
    parser.add_argument("--host", default="127.0.0.1", help="the interface to bind to (streamable-http only)")
    parser.add_argument("--port", type=int, default=5101, help="the port to listen on (streamable-http only)")
    args = parser.parse_args()

    # configure logging to stderr, keeping stdout free for the stdio MCP protocol
    configure_logging()

    # create and run the server, terminating hard on SIGINT/SIGTERM
    _install_terminating_signal_handlers()
    server = McpServerFactory(AnalysisContext.create_default()).create_server(host=args.host, port=args.port)
    server.run(transport=args.transport)


def _install_terminating_signal_handlers() -> None:
    """
    Installs SIGINT/SIGTERM handlers that terminate the process immediately.

    The default graceful shutdown waits for open MCP sessions to drain, which long-lived client
    connections never do, rendering the server unresponsive to Ctrl+C. Immediate termination is
    safe here: all database transactions are short and SQLite (in WAL mode) is crash-consistent.
    """

    def terminate(signum: int, _frame: FrameType | None) -> None:
        log.info("Received signal %d, terminating", signum)
        os._exit(128 + signum)

    signal.signal(signal.SIGINT, terminate)
    signal.signal(signal.SIGTERM, terminate)
