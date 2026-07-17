---
description: Cancel the active Dark Factory Ralph loop
allowed-tools: ["Bash(test -f .dark-factory/ralph-session.json:*)", "Bash(rm -f .dark-factory/ralph-session.json:*)"]
---

# Cancel Ralph

Remove the workspace-local Ralph state:

```!
state="$PWD/.dark-factory/ralph-session.json"
if test -f "$state"; then
  iteration="$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1])).get("iteration", "unknown"))' "$state" 2>/dev/null || printf unknown)"
  rm -f "$state"
  printf 'Cancelled Ralph loop (was at iteration %s)\n' "$iteration"
else
  printf 'No active Ralph loop found.\n'
fi
```
