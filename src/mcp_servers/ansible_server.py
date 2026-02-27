"""MCP server for Ansible and OpenShift operations.

This server provides tools for running Ansible playbooks and interacting
with OpenShift/Kubernetes clusters. Tools are dynamically loaded from
embedded metadata in playbook files (# mcp_meta: comments).
"""

import asyncio
import json
import re
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import structlog
import yaml
from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import TextContent, Tool

from src.config import Settings

logger = structlog.get_logger(__name__)

MAX_OUTPUT_LENGTH = 2000


@dataclass
class AnsibleToolDef:
    """Definition of an ansible-based tool."""

    name: str
    playbook: str
    description: str
    vars: dict[str, dict[str, Any]] = field(default_factory=dict)
    destructive: bool = False
    keywords: list[str] = field(default_factory=list)

    @classmethod
    def from_dict(cls, data: dict[str, Any], playbook_filename: str) -> "AnsibleToolDef":
        """Create from a dictionary (parsed from mcp_meta comment)."""
        return cls(
            name=data["name"],
            playbook=playbook_filename,
            description=data.get("description", ""),
            vars=data.get("vars", {}),
            destructive=data.get("destructive", False),
            keywords=data.get("keywords", []),
        )

    def build_input_schema(self) -> dict[str, Any]:
        """Build MCP input schema from vars definition."""
        properties = {}
        required = []

        for var_name, var_def in self.vars.items():
            properties[var_name] = {
                "type": var_def.get("type", "string"),
                "description": var_def.get("description", ""),
            }
            if var_def.get("required", False):
                required.append(var_name)

        # Add confirm parameter for destructive tools
        if self.destructive:
            properties["confirm"] = {
                "type": "boolean",
                "description": "Set to true to confirm execution of this destructive operation. "
                "If false or omitted, runs in check mode (dry run) first.",
            }

        return {
            "type": "object",
            "properties": properties,
            "required": required,
        }


def parse_mcp_meta(playbook_path: Path) -> dict[str, Any] | None:
    """Parse mcp_meta comment block from a playbook file.

    Looks for a comment block at the top of the file in the format:
        # mcp_meta:
        #   name: tool_name
        #   description: Tool description
        #   destructive: false
        #   vars:
        #     var_name: {type: string, required: true, description: "Var desc"}

    Returns:
        Parsed metadata dict or None if no mcp_meta block found.
    """
    try:
        content = playbook_path.read_text()
    except Exception as e:
        logger.warning("Failed to read playbook", path=str(playbook_path), error=str(e))
        return None

    # Look for # mcp_meta: block at the start of the file
    lines = content.split("\n")
    meta_lines = []
    in_meta_block = False

    for line in lines:
        stripped = line.strip()

        # Start of mcp_meta block
        if stripped == "# mcp_meta:":
            in_meta_block = True
            meta_lines.append("mcp_meta:")
            continue

        # Inside mcp_meta block - collect indented comment lines
        if in_meta_block:
            if stripped.startswith("#") and len(stripped) > 1:
                # Remove the leading "# " or "#" and preserve indentation
                comment_content = line.lstrip("#").rstrip()
                if comment_content.startswith(" "):
                    comment_content = comment_content[1:]  # Remove single leading space
                # Check if this looks like a continuation (indented)
                if comment_content and (comment_content[0] == " " or comment_content.strip()):
                    meta_lines.append(comment_content)
                else:
                    break
            else:
                # End of comment block (non-comment line or empty comment)
                break

    if not meta_lines:
        return None

    # Parse the collected lines as YAML
    try:
        yaml_content = "\n".join(meta_lines)
        parsed = yaml.safe_load(yaml_content)
        if parsed and "mcp_meta" in parsed:
            return parsed["mcp_meta"]
    except yaml.YAMLError as e:
        logger.warning("Failed to parse mcp_meta YAML", path=str(playbook_path), error=str(e))

    return None


def load_ansible_tools(playbooks_dir: Path) -> list[AnsibleToolDef]:
    """Load ansible tools by scanning playbook files for mcp_meta comments."""
    if not playbooks_dir.exists():
        logger.warning("Playbooks directory not found", path=str(playbooks_dir))
        return []

    tools = []

    # Scan all .yml and .yaml files
    for pattern in ("*.yml", "*.yaml"):
        for playbook_path in playbooks_dir.glob(pattern):
            meta = parse_mcp_meta(playbook_path)
            if meta and "name" in meta:
                try:
                    tool = AnsibleToolDef.from_dict(meta, playbook_path.name)
                    tools.append(tool)
                    logger.debug(
                        "Loaded tool from playbook",
                        tool=tool.name,
                        playbook=playbook_path.name,
                        destructive=tool.destructive,
                    )
                except (KeyError, TypeError) as e:
                    logger.warning(
                        "Invalid mcp_meta in playbook",
                        path=str(playbook_path),
                        error=str(e),
                    )

    logger.info("Loaded ansible tools from playbooks", count=len(tools))
    return tools


def run_playbook(
    playbook_path: Path,
    extra_vars: dict[str, Any],
    settings: Settings,
    check_mode: bool = False,
) -> tuple[bool, str, str]:
    """Execute an Ansible playbook using subprocess.

    Args:
        playbook_path: Path to the playbook file.
        extra_vars: Dictionary of extra variables to pass to the playbook.
        settings: Application settings.
        check_mode: If True, run in check mode (dry run).

    Returns:
        Tuple of (success, stdout, stderr).
    """
    abs_playbook_path = playbook_path.resolve()
    cmd = [settings.ansible_playbook_cmd, str(abs_playbook_path)]

    if settings.ansible_inventory:
        abs_inventory = Path(settings.ansible_inventory).resolve()
        cmd.extend(["-i", str(abs_inventory)])

    if extra_vars:
        extra_vars_json = json.dumps(extra_vars)
        cmd.extend(["-e", extra_vars_json])

    if check_mode:
        cmd.append("--check")

    logger.info("Running ansible-playbook", cmd=cmd)

    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=settings.ansible_timeout,
        )

        stdout = result.stdout
        stderr = result.stderr

        if len(stdout) > MAX_OUTPUT_LENGTH:
            stdout = stdout[:MAX_OUTPUT_LENGTH] + "\n... (output truncated)"

        if len(stderr) > MAX_OUTPUT_LENGTH:
            stderr = stderr[:MAX_OUTPUT_LENGTH] + "\n... (output truncated)"

        return result.returncode == 0, stdout, stderr

    except subprocess.TimeoutExpired:
        return False, "", f"Playbook execution timed out after {settings.ansible_timeout} seconds"
    except FileNotFoundError:
        return False, "", f"ansible-playbook command not found at: {settings.ansible_playbook_cmd}"
    except Exception as e:
        return False, "", f"Error executing playbook: {str(e)}"


def _get_available_playbooks(settings: Settings) -> list[str]:
    """Get list of available playbooks."""
    playbooks_dir = settings.ansible_playbooks_dir
    if not playbooks_dir.exists():
        return []

    return sorted(
        f.name for f in playbooks_dir.glob("*.yml") if f.is_file()
    ) + sorted(
        f.name for f in playbooks_dir.glob("*.yaml") if f.is_file()
    )


def create_server(settings: Settings) -> Server:
    """Create and configure the Ansible MCP server.

    Args:
        settings: Application settings.

    Returns:
        Configured MCP server.
    """
    server = Server("sre-copilot-ansible")

    # Load tools from playbook mcp_meta comments
    ansible_tools = load_ansible_tools(settings.ansible_playbooks_dir)
    tools_by_name = {tool.name: tool for tool in ansible_tools}

    @server.list_tools()
    async def list_tools() -> list[Tool]:
        """List available tools - dynamically generated from playbook mcp_meta."""
        tools = []

        # Add tools from playbook metadata
        for tool_def in ansible_tools:
            # Indicate destructive tools in description
            description = tool_def.description
            if tool_def.destructive:
                description = f"[DESTRUCTIVE] {description}"

            tools.append(
                Tool(
                    name=tool_def.name,
                    description=description,
                    inputSchema=tool_def.build_input_schema(),
                )
            )

        # Add generic utility tools
        tools.extend([
            Tool(
                name="run_playbook",
                description="Run any Ansible playbook with custom variables",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "playbook": {
                            "type": "string",
                            "description": "Name of the playbook file (e.g., 'my_playbook.yml')",
                        },
                        "extra_vars": {
                            "type": "object",
                            "description": "Extra variables to pass to the playbook",
                            "additionalProperties": True,
                        },
                        "check_mode": {
                            "type": "boolean",
                            "description": "Run in check mode (dry run)",
                            "default": False,
                        },
                    },
                    "required": ["playbook"],
                },
            ),
            Tool(
                name="list_playbooks",
                description="List all available Ansible playbooks",
                inputSchema={
                    "type": "object",
                    "properties": {},
                },
            ),
        ])

        return tools

    @server.call_tool()
    async def call_tool(name: str, arguments: dict[str, Any]) -> list[TextContent]:
        """Handle tool calls."""
        logger.info("Tool called", tool=name, arguments=arguments)

        # Check if it's a registered ansible tool
        if name in tools_by_name:
            return _execute_ansible_tool(tools_by_name[name], arguments, settings)

        # Handle built-in tools
        if name == "run_playbook":
            return _run_playbook(arguments, settings)
        elif name == "list_playbooks":
            return _list_playbooks(settings)
        else:
            return [TextContent(type="text", text=f"Unknown tool: {name}")]

    return server


def _execute_ansible_tool(
    tool_def: AnsibleToolDef, arguments: dict[str, Any], settings: Settings
) -> list[TextContent]:
    """Execute an ansible tool from the registry."""
    # Validate required vars
    extra_vars = {}
    for var_name, var_def in tool_def.vars.items():
        value = arguments.get(var_name)
        if var_def.get("required") and not value:
            return [TextContent(type="text", text=f"Error: {var_name} is required")]
        if value is not None:
            extra_vars[var_name] = value

    playbook_path = settings.ansible_playbooks_dir / tool_def.playbook

    if not playbook_path.exists():
        return [TextContent(type="text", text=f"Error: Playbook not found: {tool_def.playbook}")]

    # Handle destructive tools - require confirmation or run in check mode
    confirmed = arguments.get("confirm", False)
    check_mode = tool_def.destructive and not confirmed

    if tool_def.destructive and not confirmed:
        logger.info(
            "Running destructive playbook in check mode",
            playbook=tool_def.playbook,
            tool=tool_def.name,
        )

    success, stdout, stderr = run_playbook(playbook_path, extra_vars, settings, check_mode)

    target = extra_vars.get("target_hosts", "")

    # Build response with check mode indicator
    if check_mode:
        prefix = f"[CHECK MODE - No changes made] {tool_def.name} on {target}\n"
        suffix = "\n\nTo execute for real, call again with confirm=true"
    else:
        prefix = ""
        suffix = ""

    if success:
        return [TextContent(type="text", text=f"{prefix}OK: {target}\n{stdout}{suffix}")]
    else:
        return [TextContent(type="text", text=f"{prefix}FAILED: {target}\n{stderr}\n{stdout}{suffix}")]


def _run_playbook(arguments: dict[str, Any], settings: Settings) -> list[TextContent]:
    """Run a specified Ansible playbook."""
    playbook_name = arguments.get("playbook")
    extra_vars = arguments.get("extra_vars", {})
    check_mode = arguments.get("check_mode", False)

    if not playbook_name:
        return [TextContent(type="text", text="Error: playbook name is required")]

    playbook_path = settings.ansible_playbooks_dir / playbook_name

    if not playbook_path.exists():
        available = _get_available_playbooks(settings)
        return [
            TextContent(
                type="text",
                text=f"Error: Playbook '{playbook_name}' not found.\n\nAvailable playbooks:\n"
                + "\n".join(f"  - {p}" for p in available),
            )
        ]

    success, stdout, stderr = run_playbook(playbook_path, extra_vars, settings, check_mode)

    if success:
        response = f"OK: {playbook_name}\n{stdout}"
    else:
        response = f"FAILED: {playbook_name}\n{stderr}\n{stdout}"

    return [TextContent(type="text", text=response)]


def _list_playbooks(settings: Settings) -> list[TextContent]:
    """List available Ansible playbooks."""
    playbooks = _get_available_playbooks(settings)

    if not playbooks:
        return [
            TextContent(
                type="text",
                text=f"No playbooks found in {settings.ansible_playbooks_dir}",
            )
        ]

    response = "Playbooks:\n" + "\n".join(f"- {p}" for p in playbooks)
    return [TextContent(type="text", text=response)]


async def run_server(settings: Settings) -> None:
    """Run the Ansible MCP server.

    Args:
        settings: Application settings.
    """
    server = create_server(settings)

    async with stdio_server() as (read_stream, write_stream):
        logger.info("Starting MCP Ansible server")
        await server.run(
            read_stream,
            write_stream,
            server.create_initialization_options(),
        )


if __name__ == "__main__":
    from src.config import get_settings

    settings = get_settings()
    asyncio.run(run_server(settings))
