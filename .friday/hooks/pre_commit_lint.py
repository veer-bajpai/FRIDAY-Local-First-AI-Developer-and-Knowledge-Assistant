import json
import re
import subprocess
import sys

event = json.load(sys.stdin)
tool = event.get("tool", "")
command = (event.get("args") or {}).get("command", "")
is_commit = tool == "git_commit" or (tool in {"bash", "run_bash"} and re.search(r"\bgit\s+commit\b", command))
if is_commit:
    result = subprocess.run(
        [sys.executable, "-m", "ruff", "check", "."],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode:
        print(result.stdout or result.stderr, file=sys.stderr)
        if "No module named ruff" in result.stderr:
            print("Install backend/requirements-agent-optional.txt to enable the commit lint hook.", file=sys.stderr)
        sys.exit(result.returncode)