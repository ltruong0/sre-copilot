"""MCP server for Ansible and OpenShift operations.

This server provides tools for running Ansible playbooks and interacting
with OpenShift/Kubernetes clusters.
"""

import asyncio
import json
import subprocess
from pathlib import Path
from typing import Any

import structlog
from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import TextContent, Tool

from src.config import Settings

logger = structlog.get_logger(__name__)

MAX_OUTPUT_LENGTH = 4000


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
    # Resolve to absolute path
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


def create_server(settings: Settings) -> Server:
    """Create and configure the Ansible MCP server.

    Args:
        settings: Application settings.

    Returns:
        Configured MCP server.
    """
    server = Server("sre-copilot-ansible")

    @server.list_tools()
    async def list_tools() -> list[Tool]:
        """List available tools."""
        return [
            Tool(
                name="check_security_vulnerabilities",
                description="Run security vulnerability check on target hosts. "
                "This executes the security fix collector to identify vulnerabilities.",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "target_hosts": {
                            "type": "string",
                            "description": "Target hosts or host group to check (e.g., 'webservers', 'all', 'host1.example.com')",
                        },
                        "check_mode": {
                            "type": "boolean",
                            "description": "Run in check mode (dry run) without making changes",
                            "default": False,
                        },
                    },
                    "required": ["target_hosts"],
                },
            ),
            Tool(
                name="run_playbook",
                description="Run an Ansible playbook with specified variables",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "playbook": {
                            "type": "string",
                            "description": "Name of the playbook to run (e.g., 'check_security_vulnerabilities.yml')",
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
                description="List available Ansible playbooks",
                inputSchema={
                    "type": "object",
                    "properties": {},
                },
            ),
            Tool(
                name="check_service_status",
                description="Check the status of a service in OpenShift (STUB - not implemented)",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "namespace": {
                            "type": "string",
                            "description": "The namespace/project name",
                        },
                        "service": {
                            "type": "string",
                            "description": "The service name",
                        },
                    },
                    "required": ["namespace", "service"],
                },
            ),
            Tool(
                name="get_pod_logs",
                description="Get logs from a pod (STUB - not implemented)",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "namespace": {
                            "type": "string",
                            "description": "The namespace/project name",
                        },
                        "pod": {
                            "type": "string",
                            "description": "The pod name",
                        },
                        "lines": {
                            "type": "integer",
                            "description": "Number of log lines to retrieve",
                            "default": 100,
                        },
                    },
                    "required": ["namespace", "pod"],
                },
            ),
            Tool(
                name="describe_resource",
                description="Describe a Kubernetes resource (STUB - not implemented)",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "namespace": {
                            "type": "string",
                            "description": "The namespace/project name",
                        },
                        "resource_type": {
                            "type": "string",
                            "description": "The resource type (e.g., pod, deployment, service)",
                        },
                        "name": {
                            "type": "string",
                            "description": "The resource name",
                        },
                    },
                    "required": ["namespace", "resource_type", "name"],
                },
            ),
        ]

    @server.call_tool()
    async def call_tool(name: str, arguments: dict[str, Any]) -> list[TextContent]:
        """Handle tool calls."""
        logger.info("Tool called", tool=name, arguments=arguments)

        if name == "check_security_vulnerabilities":
            return _check_security_vulnerabilities(arguments, settings)
        elif name == "run_playbook":
            return _run_playbook(arguments, settings)
        elif name == "list_playbooks":
            return _list_playbooks(settings)
        elif name == "check_service_status":
            return _stub_check_service_status(arguments)
        elif name == "get_pod_logs":
            return _stub_get_pod_logs(arguments)
        elif name == "describe_resource":
            return _stub_describe_resource(arguments)
        else:
            return [TextContent(type="text", text=f"Unknown tool: {name}")]

    return server


def _check_security_vulnerabilities(
    arguments: dict[str, Any], settings: Settings
) -> list[TextContent]:
    """Check security vulnerabilities on target hosts."""
    target_hosts = arguments.get("target_hosts")
    check_mode = arguments.get("check_mode", False)

    if not target_hosts:
        return [TextContent(type="text", text="Error: target_hosts is required")]

    playbook_path = settings.ansible_playbooks_dir / "check_security_vulnerabilities.yml"

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
        response = f"""**Security Vulnerability Check - {mode_str}**

Target Hosts: {target_hosts}
Status: ✓ Completed successfully

**Output:**
```
{stdout}
```"""
    else:
        response = f"""**Security Vulnerability Check - {mode_str}**

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

    response = f"""**Available Playbooks**

Directory: {settings.ansible_playbooks_dir}

Playbooks:
"""
    for playbook in playbooks:
        response += f"  - {playbook}\n"

    return [TextContent(type="text", text=response)]


def _stub_check_service_status(arguments: dict[str, Any]) -> list[TextContent]:
    """Stub implementation for check_service_status."""
    namespace = arguments.get("namespace", "unknown")
    service = arguments.get("service", "unknown")

    response = f"""**STUB: check_service_status**

This is a stub implementation. In production, this would:
1. Connect to OpenShift cluster
2. Check service: {service} in namespace: {namespace}
3. Return endpoints, selectors, and pod status

To implement:
- Add kubernetes/openshift client
- Query service and related resources
- Aggregate health information"""

    return [TextContent(type="text", text=response)]


def _stub_get_pod_logs(arguments: dict[str, Any]) -> list[TextContent]:
    """Stub implementation for get_pod_logs."""
    namespace = arguments.get("namespace", "unknown")
    pod = arguments.get("pod", "unknown")
    lines = arguments.get("lines", 100)

    response = f"""**STUB: get_pod_logs**

This is a stub implementation. In production, this would:
1. Connect to OpenShift cluster
2. Get logs for pod: {pod} in namespace: {namespace}
3. Return last {lines} lines

To implement:
- Add kubernetes client
- Stream logs from pod
- Handle multi-container pods
- Support previous container logs"""

    return [TextContent(type="text", text=response)]


def _stub_describe_resource(arguments: dict[str, Any]) -> list[TextContent]:
    """Stub implementation for describe_resource."""
    namespace = arguments.get("namespace", "unknown")
    resource_type = arguments.get("resource_type", "unknown")
    name = arguments.get("name", "unknown")

    response = f"""**STUB: describe_resource**

This is a stub implementation. In production, this would:
1. Connect to OpenShift cluster
2. Describe {resource_type}/{name} in namespace: {namespace}
3. Return detailed resource information

To implement:
- Add kubernetes client
- Get resource with full details
- Format output similar to kubectl describe
- Include events and conditions"""

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
