"""Decides whether a tool call runs automatically, needs the user's approval, or is refused."""
from __future__ import annotations

import json
import re
from pathlib import Path

from .config import HOME, MODES
from .tools import Tool

# Read-only shell commands that never need approval (and only when there is no chaining/redirection).
SAFE_BASH = re.compile(
    r"^(ls|pwd|cat|head|tail|wc|echo|which|tree|date|whoami|file|stat|du|df|"
    r"git (status|diff|log|branch|show|remote -v|rev-parse|ls-files)|"
    r"(python|python3|node|npm|pip|cargo|go|java) (-V|--version|-v|list|ls))\b"
)
CHAINING = re.compile(r"[;&|<>`$]|\$\(|\n")

# Refused outright, in every mode.
DANGEROUS = [
    (re.compile(r"\brm\s+(-\w*\s+)*-\w*[rf]\w*\s+(/|~|\*|\$HOME)(\s|$)"), "recursive delete of a root/home path"),
    (re.compile(r"\bsudo\b"), "sudo is not allowed"),
    (re.compile(r"\b(mkfs|fdisk|dd\s+if=)"), "disk-level commands are not allowed"),
    (re.compile(r":\(\)\s*\{"), "fork bomb"),
    (re.compile(r"(curl|wget)[^|]*\|\s*(sudo\s+)?(sh|bash|zsh|python)"), "piping downloads into a shell"),
    (re.compile(r"\bgit\s+push\b.*(--force|-f)\b"), "force-push must be done by the user"),
    (re.compile(r"\bformat\s+[a-z]:", re.I), "disk format"),
    (re.compile(r"\bRemove-Item\b.*-Recurse.*\b[A-Z]:\\\s*$", re.I), "recursive delete of a drive root"),
]

EDIT_TOOLS = {"edit_file", "write_file"}

def bash_prefix(cmd: str) -> str:
    """'git commit -m x' -> 'git commit'; 'pytest -q' -> 'pytest'. Used for 'always allow' rules."""
    toks = cmd.strip().split()
    if not toks:
        return ""
    if toks[0] in {"git", "npm", "pnpm", "yarn", "pip", "docker", "cargo", "go"} and len(toks) > 1:
        return f"{toks[0]} {toks[1]}"
    return toks[0]

def rule_for(tool: Tool, args: dict) -> str:
    return f"bash:{bash_prefix(args.get('command', ''))}" if tool.name == "bash" else tool.name

class Permissions:
    def __init__(self, root: Path, mode: str = "default"):
        self.root = root
        self.mode = mode if mode in MODES else "default"
        self.allow: set[str] = set()
        self.deny: set[str] = set()
        for f in (HOME / "settings.json", root / ".friday" / "settings.json"):
            try:
                data = json.loads(f.read_text())
                self.allow |= set(data.get("allow", []))
                self.deny |= set(data.get("deny", []))
            except Exception:
                pass

    # ---- decision -----------------------------------------------------------
    def check(self, tool: Tool, args: dict) -> tuple[str, str]:
        """Returns ("allow"|"ask"|"deny", reason)."""
        rule = rule_for(tool, args)
        cmd = args.get("command", "") if tool.name == "bash" else ""

        if tool.name == "bash":
            for rx, why in DANGEROUS:
                if rx.search(cmd):
                    return "deny", f"Blocked: {why}."
        if rule in self.deny or tool.name in self.deny:
            return "deny", f"Blocked by a deny rule ({rule})."

        safe_cmd = tool.name == "bash" and SAFE_BASH.match(cmd.strip()) and not CHAINING.search(cmd)

        if self.mode == "plan":
            if tool.name == "exit_plan_mode":
                return "ask", ""
            if tool.read_only or safe_cmd:
                return "allow", ""
            return "deny", ("Plan mode is read-only. Research with read tools, then call exit_plan_mode "
                            "with your plan for the user to approve.")

        if tool.name == "exit_plan_mode":
            return "allow", ""
        if tool.read_only or safe_cmd or self.mode == "bypass":
            return "allow", ""
        if self.mode == "acceptEdits" and tool.name in EDIT_TOOLS:
            return "allow", ""
        if rule in self.allow or tool.name in self.allow:
            return "allow", ""
        return "ask", ""

    # ---- remember "always allow" -------------------------------------------
    def remember(self, tool: Tool, args: dict, persist: bool = True) -> str:
        rule = rule_for(tool, args)
        self.allow.add(rule)
        if persist:
            f = self.root / ".friday" / "settings.json"
            try:
                f.parent.mkdir(exist_ok=True)
                data = json.loads(f.read_text()) if f.exists() else {}
                data.setdefault("allow", [])
                if rule not in data["allow"]:
                    data["allow"].append(rule)
                f.write_text(json.dumps(data, indent=2))
            except OSError:
                pass
        return rule

    # bash rules are prefix rules, so "bash:git commit" also matches later "git commit -m y"
    def matches_allow(self, tool: Tool, args: dict) -> bool:
        return rule_for(tool, args) in self.allow
