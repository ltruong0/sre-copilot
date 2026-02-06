"""MCP server for Ansible and OpenShift operations.

This server provides tools for running Ansible playbooks and interacting
with OpenShift/Kubernetes clusters. Tools are dynamically loaded from
playbooks/ansible_tools.json.
"""

import asyncio
import json
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import structlog
from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import TextContent, Tool

from src.config import Settings

logger = structlog.get_logger(__name__)

MAX_OUTPUT_LENGTH = 4000


@dataclass
class AnsibleToolDef:
    """Definition of an ansible-based tool."""

    name: str
    playbook: str
    description: str
    keywords: list[str]

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "AnsibleToolDef":
        """Create from a dictionary."""
        return cls(
            name=data["name"],
            playbook=data["playbook"],
            description=data["description"],
            keywords=data.get("keywords", []),
        )


def load_ansible_tools(playbooks_dir: Path) -> list[AnsibleToolDef]:
    """Load ansible tools from the JSON config file."""
    config_path = playbooks_dir / "ansible_tools.json"

    if not config_path.exists():
        logger.warning("ansible_tools.json not found", path=str(config_path))
        return []

    try:
        with open(config_path) as f:
            data = json.load(f)

        tools = [AnsibleToolDef.from_dict(t) for t in data.get("tools", [])]
        logger.info("Loaded ansible tools for MCP", count=len(tools))
        return tools

    except (json.JSONDecodeError, KeyError) as e:
        logger.error("Failed to load ansible_tools.json", error=str(e))
        return []


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

    # Load tools from JSON config
    ansible_tools = load_ansible_tools(settings.ansible_playbooks_dir)
    tools_by_name = {tool.name: tool for tool in ansible_tools}

    @server.list_tools()
    async def list_tools() -> list[Tool]:
        """List available tools - dynamically generated from ansible_tools.json."""
        tools = []

        # Add tools from JSON config
        for tool_def in ansible_tools:
            tools.append(
                Tool(
                    name=tool_def.name,
                    description=tool_def.description,
                    inputSchema={
                        "type": "object",
                        "properties": {
                            "target_hosts": {
                                "type": "string",
                                "description": "Target hosts or host group (e.g., 'all', 'webservers', 'host1.example.com')",
                            },
                            "check_mode": {
                                "type": "boolean",
                                "description": "Run in check mode (dry run) without making changes",
                                "default": False,
                            },
                        },
                        "required": ["target_hosts"],
                    },
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
    target_hosts = arguments.get("target_hosts")
    check_mode = arguments.get("check_mode", False)

    if not target_hosts:
        return [TextContent(type="text", text="Error: target_hosts is required")]

    playbook_path = settings.ansible_playbooks_dir / tool_def.playbook

    if not playbook_path.exists():
        return [
            TextContent(
                type="text",
                text=f"Error: Playbook not found at {playbook_path}",
            )
        ]

    extra_vars = {"target_hosts": target_hosts}
    mode_str = "CHECK MODE (dry run)" if check_mode else "EXECUTE MODE"

    success, stdout, stderr = run_playbook(playbook_path, extra_vars, settings, check_mode)

    if success:
        response = f"""**{tool_def.description}**

Mode: {mode_str}
Target Hosts: {target_hosts}
Status: ✓ Completed successfully

**Output:**
```
{stdout}
```"""
    else:
        response = f"""**{tool_def.description}**

Mode: {mode_str}
Target Hosts: {target_hosts}
Status: ✗ Failed

**Error:**
```
{stderr}
```

**Output:**
```
{stdout}
```"""

    return [TextContent(type="text", text=response)]


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

    mode_str = "CHECK MODE (dry run)" if check_mode else "EXECUTE MODE"

    success, stdout, stderr = run_playbook(playbook_path, extra_vars, settings, check_mode)

    if success:
        response = f"""**Playbook Execution - {mode_str}**

Playbook: {playbook_name}
Extra Variables: {json.dumps(extra_vars) if extra_vars else 'None'}
Status: ✓ Completed successfully

**Output:**
```
{stdout}
```"""
    else:
        response = f"""**Playbook Execution - {mode_str}**

Playbook: {playbook_name}
Extra Variables: {json.dumps(extra_vars) if extra_vars else 'None'}
Status: ✗ Failed

**Error:**
```
{stderr}
```

**Output:**
```
{stdout}
```"""

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

    # Also load tool definitions to show which have tools
    ansible_tools = load_ansible_tools(settings.ansible_playbooks_dir)
    tool_playbooks = {t.playbook: t.name for t in ansible_tools}

    response = f"""**Available Playbooks**

Directory: {settings.ansible_playbooks_dir}

Playbooks:
"""
    for playbook in playbooks:
        if playbook in tool_playbooks:
            response += f"  - {playbook} (tool: {tool_playbooks[playbook]})\n"
        else:
            response += f"  - {playbook}\n"

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
