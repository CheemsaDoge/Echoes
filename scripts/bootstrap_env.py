#!/usr/bin/env python3
"""Create fresh local secrets. Never overwrite an existing environment file."""
from pathlib import Path
import secrets

root = Path(__file__).resolve().parents[1]
target = root / '.env'
if target.exists():
    raise SystemExit('.env already exists; preserve it or move it explicitly first.')
text = (root / '.env.example').read_text()
text = text.replace('REPLACE_WITH_RANDOM_API_TOKEN', secrets.token_urlsafe(32))
text = text.replace('REPLACE_WITH_DISTINCT_RANDOM_MCP_TOKEN', secrets.token_urlsafe(32))
target.write_text(text)
target.chmod(0o600)
print('Created .env with distinct random API and read-only MCP tokens. Do not commit it.')
