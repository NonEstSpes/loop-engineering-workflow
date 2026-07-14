#!/usr/bin/env bash
# Regenerate src/api/schema.ts from the running daemon's OpenAPI schema.
# Requires the daemon to be running on localhost:8787.
set -euo pipefail
npx openapi-typescript http://localhost:8787/openapi.json -o src/api/schema.ts
echo "Wrote src/api/schema.ts"
