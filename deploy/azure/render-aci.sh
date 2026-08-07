#!/usr/bin/env bash
#
# Render aci.template.yaml to stdout with real values substituted.
#
# One renderer shared by deploy.sh and the GitHub Actions workflow, so the two
# paths can't drift apart.
#
# Required in the environment:
#   LOCATION GROUP_NAME DNS_LABEL ACR_SERVER ACR_USERNAME ACR_PASSWORD
#   TAG MONGO_URI DEPLOY_SOURCE

set -euo pipefail

TEMPLATE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/aci.template.yaml"

for var in LOCATION GROUP_NAME DNS_LABEL ACR_SERVER ACR_USERNAME ACR_PASSWORD TAG MONGO_URI DEPLOY_SOURCE; do
  if [[ -z "${!var:-}" ]]; then
    echo "render-aci.sh: $var is not set" >&2
    exit 1
  fi
done

# Substituted in Python, not sed: a Cosmos connection string contains & and /,
# both of which mean something to sed's replacement syntax and would corrupt
# the value silently.
TEMPLATE_PATH="$TEMPLATE" python3 <<'PY'
import os

placeholders = [
    "LOCATION", "GROUP_NAME", "DNS_LABEL", "ACR_SERVER", "ACR_USERNAME",
    "ACR_PASSWORD", "TAG", "MONGO_URI", "DEPLOY_SOURCE",
]

with open(os.environ["TEMPLATE_PATH"]) as fh:
    text = fh.read()

for name in placeholders:
    text = text.replace(f"__{name}__", os.environ[name])

leftover = [p for p in placeholders if f"__{p}__" in text]
if leftover:
    raise SystemExit(f"render-aci.sh: unsubstituted placeholders: {leftover}")

print(text, end="")
PY
