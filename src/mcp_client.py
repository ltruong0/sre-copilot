"""MCP client for connecting to external MCP servers."""

import asyncio
import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import structlog
from dotenv import dotenv_values
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

logger = structlog.get_logger(__name__)


@dataclass
class MCPServerConfig:
    """Configuration for an external MCP server."""

    name: str
    description: str
    command: str
    args: list[str] = field(default_factory=list)
    env: dict[str, str] = field(default_factory=dict)
    enabled: bool = True

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "MCPServerConfig":
        """Create from dictionary."""
        return cls(
            name=data["name"],
            description=data.get("description", ""),
            command=data["command"],
            args=data.get("args", []),
            env=data.get("env", {}),
            enabled=data.get("enabled", True),
        )

    def resolve_env(self) -> dict[str, str]:
        """Resolve environment variables (supports ${VAR} syntax).

        Checks both os.environ and .env file for values.
        """
        # Load .env file values as fallback
        dotenv_vars = dotenv_values(".env")

        resolved = {}
        for key, value in self.env.items():
            if value.startswith("${") and value.endswith("}"):
                env_var = value[2:-1]
                # Check os.environ first, then .env file
                resolved[key] = os.environ.get(env_var) or dotenv_vars.get(env_var, "")
            else:
                resolved[key] = value
        return resolved


@dataclass
class MCPTool:
    """A tool from an external MCP server."""

    name: str
    description: str
    server_name: str
    input_schema: dict[str, Any] = field(default_factory=dict)


class MCPClientManager:
    """Manages connections to external MCP servers."""

    def __init__(self, config_path: Path | None = None):
        """Initialize the MCP client manager.

        Args:
            config_path: Path to mcp_servers.json config file.
        """
        self._config_path = config_path or Path("mcp_servers.json")
        self._servers: dict[str, MCPServerConfig] = {}
        self._sessions: dict[str, ClientSession] = {}
        self._tools: dict[str, MCPTool] = {}
        self._read_streams: dict[str, Any] = {}
        self._write_streams: dict[str, Any] = {}

    def load_config(self) -> list[MCPServerConfig]:
        """Load server configurations from JSON file."""
        if not self._config_path.exists():
            logger.warning("MCP servers config not found", path=str(self._config_path))
            return []

        try:
            with open(self._config_path) as f:
                data = json.load(f)

            servers = [
                MCPServerConfig.from_dict(s)
                for s in data.get("servers", [])
                if s.get("enabled", True)
            ]
            self._servers = {s.name: s for s in servers}
            logger.info("Loaded MCP server configs", count=len(servers))
            return servers

        except (json.JSONDecodeError, KeyError) as e:
            logger.error("Failed to load MCP servers config", error=str(e))
            return []

    async def connect_all(self) -> dict[str, list[MCPTool]]:
        """Connect to all configured MCP servers and discover tools.

        Returns:
            Dict mapping server name to list of tools.
        """
        if not self._servers:
            self.load_config()

        tools_by_server: dict[str, list[MCPTool]] = {}

        for name, config in self._servers.items():
            try:
                tools = await self._connect_server(config)
                tools_by_server[name] = tools
                for tool in tools:
                    self._tools[f"{name}.{tool.name}"] = tool
                logger.info(
                    "Connected to MCP server",
                    server=name,
                    tools=len(tools),
                )
            except Exception as e:
                logger.error(
                    "Failed to connect to MCP server",
                    server=name,
                    error=str(e),
                )

        return tools_by_server

    async def _connect_server(self, config: MCPServerConfig) -> list[MCPTool]:
        """Connect to a single MCP server."""
        # Build environment with resolved variables
        env = {**os.environ, **config.resolve_env()}

        server_params = StdioServerParameters(
            command=config.command,
            args=config.args,
            env=env,
        )

        # Use the stdio_client as async context manager
        # We need to keep the context manager alive, so store it
        stdio_cm = stdio_client(server_params)
        read_stream, write_stream = await stdio_cm.__aenter__()

        # Store context manager for cleanup
        if not hasattr(self, "_stdio_cms"):
            self._stdio_cms: dict[str, Any] = {}
        self._stdio_cms[config.name] = stdio_cm

        self._read_streams[config.name] = read_stream
        self._write_streams[config.name] = write_stream

        # Create and initialize session
        session = ClientSession(read_stream, write_stream)
        await session.__aenter__()
        await session.initialize()

        self._sessions[config.name] = session

        # List available tools
        tools_result = await session.list_tools()
        tools = [
            MCPTool(
                name=tool.name,
                description=tool.description or "",
                server_name=config.name,
                input_schema=tool.inputSchema if hasattr(tool, "inputSchema") else {},
            )
            for tool in tools_result.tools
        ]

        return tools

    async def call_tool(
        self,
        tool_name: str,
        arguments: dict[str, Any],
    ) -> str:
        """Call a tool on an external MCP server.

        Args:
            tool_name: Full tool name (server.tool_name) or just tool_name.
            arguments: Tool arguments.

        Returns:
            Tool result as string.
        """
        # Parse server and tool name
        if "." in tool_name:
            server_name, requested_tool = tool_name.split(".", 1)
            # Look up the actual tool name from the registered tools
            full_key = tool_name
            if full_key in self._tools:
                actual_tool = self._tools[full_key].name
            else:
                # Try to find a tool that matches the requested name pattern
                actual_tool = None
                for full_name, tool in self._tools.items():
                    if tool.server_name == server_name:
                        # Check if requested_tool matches the end of the actual tool name
                        if tool.name == requested_tool or tool.name.endswith(f"_{requested_tool}"):
                            actual_tool = tool.name
                            break
                if not actual_tool:
                    return f"Tool not found: {tool_name}"
        else:
            # Find the server that has this tool
            for full_name, tool in self._tools.items():
                if tool.name == tool_name:
                    server_name = tool.server_name
                    actual_tool = tool.name
                    break
            else:
                return f"Tool not found: {tool_name}"

        session = self._sessions.get(server_name)
        if not session:
            return f"Server not connected: {server_name}"

        try:
            result = await session.call_tool(actual_tool, arguments)

            # Extract text content from result
            if hasattr(result, "content") and result.content:
                texts = []
                for content in result.content:
                    # TextContent has .text attribute
                    if hasattr(content, "text") and content.text:
                        texts.append(str(content.text))
                return "\n".join(texts) if texts else str(result)
            return str(result)

        except Exception as e:
            logger.error(
                "Tool call failed",
                tool=tool_name,
                error=str(e),
            )
            return f"Error calling {tool_name}: {str(e)}"

    def get_tools_description(self) -> str:
        """Get a description of all available external tools."""
        if not self._tools:
            return ""

        lines = ["External MCP Tools:"]
        for full_name, tool in self._tools.items():
            lines.append(f"- {full_name}: {tool.description}")
        return "\n".join(lines)

    def get_all_tools(self) -> list[MCPTool]:
        """Get all available tools."""
        return list(self._tools.values())

    async def disconnect_all(self) -> None:
        """Disconnect from all MCP servers."""
        # Close sessions first
        for name, session in self._sessions.items():
            try:
                await session.__aexit__(None, None, None)
            except Exception as e:
                logger.warning(
                    "Error closing session",
                    server=name,
                    error=str(e),
                )

        # Then close stdio connections
        if hasattr(self, "_stdio_cms"):
            for name, stdio_cm in self._stdio_cms.items():
                try:
                    await stdio_cm.__aexit__(None, None, None)
                    logger.info("Disconnected from MCP server", server=name)
                except Exception as e:
                    logger.warning(
                        "Error disconnecting from MCP server",
                        server=name,
                        error=str(e),
                    )
            self._stdio_cms.clear()

        self._sessions.clear()
        self._tools.clear()
        self._read_streams.clear()
        self._write_streams.clear()
