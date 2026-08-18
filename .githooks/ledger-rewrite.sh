#!/usr/bin/env bash
#
# Post-rewrite hook: Update ledger SHAs after rebase or amend.
#
# Worktree-safe: reads/writes ledger from shared common dir.

set -e

LEDGER_CLI="${HOME}/.claude/scripts/claude-ledger"
GIT_COMMON_DIR="$(git rev-parse --git-common-dir 2>/dev/null || git rev-parse --git-dir)"
LEDGER_FILE="${GIT_COMMON_DIR}/agent-ledger/chains.json"

if [ ! -x "$LEDGER_CLI" ] || [ ! -f "$LEDGER_FILE" ]; then
    exit 0
fi

# post-rewrite receives a list of "<old-sha> <new-sha>" on stdin
REWRITE_MAP=$(cat)

if [ -z "$REWRITE_MAP" ]; then
    exit 0
fi

echo "[ledger-rewrite] Updating agent ledger SHAs..."

# Use python to update the JSON in place efficiently
LEDGER_FILE="$LEDGER_FILE" \
REWRITE_MAP="$REWRITE_MAP" \
python3 - <<'PYEOF'
import json, os, sys

def update_entry_hash(entry, previous_hash):
    import hashlib
    content = (
        previous_hash +
        entry.get("commit_sha", "pending") +
        entry.get("instruction_hash", "") +
        ",".join(sorted(entry.get("files", []))) +
        entry.get("timestamp", "")
    )
    return "sha256:" + hashlib.sha256(content.encode()).hexdigest()

try:
    lf = os.environ['LEDGER_FILE']
    mapping = {}
    for line in os.environ['REWRITE_MAP'].strip().splitlines():
        parts = line.split()
        if len(parts) != 2:
            continue
        old, new = parts
        mapping[old] = new

    if not mapping:
        sys.exit(0)

    with open(lf) as f:
        ledger = json.load(f)

    updated_count = 0
    for session_id, chain in ledger.get('chains', {}).items():
        if chain.get('sealed'): continue

        previous_hash = chain.get('started_at', '')
        entries = chain.get('entries', [])
        chain_updated = False

        for i, entry in enumerate(entries):
            old_sha = entry.get('commit_sha')
            if old_sha in mapping:
                entry['commit_sha'] = mapping[old_sha]
                updated_count += 1
                chain_updated = True

            # Even if SHA didn't change, we must recompute hashes if any previous entry was updated
            if chain_updated:
                entry['entry_hash'] = update_entry_hash(entry, previous_hash)
                chain['chain_hash'] = entry['entry_hash']

            previous_hash = entry['entry_hash']

    if updated_count > 0:
        with open(lf, 'w') as f:
            json.dump(ledger, f, indent=2)
            f.write('\n')
        print(f'  Updated {updated_count} entries in ledger.')

    sys.exit(0)
except Exception as e:
    print(f'[ledger-rewrite] updater failed: {e}', file=sys.stderr)
    sys.exit(1)
PYEOF

exit 0
