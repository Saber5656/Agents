#!/usr/bin/env python3
"""Small safe wrapper for calling Hermes Agent from Codex/Claude workflows."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path


def run_command(args: list[str], cwd: str | None = None) -> int:
    proc = subprocess.run(
        args,
        cwd=cwd,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    payload = {
        "command": args,
        "cwd": cwd,
        "returncode": proc.returncode,
        "stdout": proc.stdout,
        "stderr": proc.stderr,
    }
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return proc.returncode


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Call Hermes Agent as a generic tool/I/O bridge."
    )
    sub = parser.add_subparsers(dest="command", required=True)

    oneshot = sub.add_parser("oneshot", help="Run Hermes and return final stdout.")
    oneshot.add_argument("--prompt", required=True)
    oneshot.add_argument("--model")
    oneshot.add_argument("--provider")
    oneshot.add_argument("--toolsets")
    oneshot.add_argument("--skills")
    oneshot.add_argument("--cwd")
    oneshot.add_argument("--timeout-note", default="caller-managed")

    send = sub.add_parser("send", help="Send a message through Hermes gateway.")
    send.add_argument("--target", required=True)
    send.add_argument("--message")
    send.add_argument("--file")
    send.add_argument("--subject")

    targets = sub.add_parser("list-targets", help="List Hermes send targets.")
    targets.add_argument("--platform")

    sub.add_parser("mcp-info", help="Print Hermes MCP bridge guidance.")
    return parser


def main() -> int:
    parser = build_parser()
    ns = parser.parse_args()

    if ns.command == "oneshot":
        cmd = ["hermes", "-z", ns.prompt]
        if ns.model:
            cmd.extend(["--model", ns.model])
        if ns.provider:
            cmd.extend(["--provider", ns.provider])
        if ns.toolsets:
            cmd.extend(["--toolsets", ns.toolsets])
        if ns.skills:
            cmd.extend(["--skills", ns.skills])
        return run_command(cmd, cwd=ns.cwd)

    if ns.command == "send":
        if bool(ns.message) == bool(ns.file):
            parser.error("send requires exactly one of --message or --file")
        cmd = ["hermes", "send", "--to", ns.target, "--json"]
        if ns.subject:
            cmd.extend(["--subject", ns.subject])
        if ns.file:
            path = Path(ns.file).expanduser()
            cmd.extend(["--file", str(path)])
        else:
            cmd.append(ns.message)
        return run_command(cmd)

    if ns.command == "list-targets":
        cmd = ["hermes", "send", "--list", "--json"]
        if ns.platform:
            cmd.append(ns.platform)
        return run_command(cmd)

    if ns.command == "mcp-info":
        info = {
            "command": "hermes mcp serve",
            "purpose": "Expose Hermes conversations, messages, live events, and send targets to an MCP client.",
            "important_tools": [
                "messages_send",
                "events_poll",
                "events_wait",
                "messages_read",
                "channels_list",
            ],
            "limitation": "A SKILL.md alone cannot receive asynchronous Hermes/Discord replies after the agent turn ends. Configure MCP, polling automation, or a callback runtime.",
        }
        print(json.dumps(info, ensure_ascii=False, indent=2))
        return 0

    parser.error(f"unknown command: {ns.command}")
    return 2


if __name__ == "__main__":
    sys.exit(main())
