#!/usr/bin/env bash
#
# Render aci.template.yaml to stdout with real values substituted.
#
# One renderer shared by deploy.sh and the GitHub Actions workflow, so the two
# paths can't drift apart.
#
# Required in the environment:
#   LOCATION GROUP_NAME DNS_LABEL ACR_SERVER ACR_USERNAME ACR_PASSWORD
#   TAG MONGO_URI DEPLOY_SOURCE STORAGE_ACCOUNT STORAGE_KEY
#
# FQDN is derived from DNS_LABEL and LOCATION unless set explicitly.

set -euo pipefail

TEMPLATE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/aci.template.yaml"

# The public hostname Caddy requests a certificate for. Derived rather than
# configured: it is whatever the DNS label and region already imply, and a
# mismatch here means ACME fails for a name nobody can reach.
export FQDN="${FQDN:-${DNS_LABEL}.${LOCATION}.azurecontainer.io}"

for var in LOCATION GROUP_NAME DNS_LABEL ACR_SERVER ACR_USERNAME ACR_PASSWORD \
           TAG MONGO_URI DEPLOY_SOURCE FQDN STORAGE_ACCOUNT STORAGE_KEY; do
  if [[ -z "${!var:-}" ]]; then
    echo "render-aci.sh: $var is not set" >&2
    exit 1
  fi
done

# Substituted in Python, not sed: a MongoDB connection string routinely
# contains & and /, both of which mean something to sed's replacement syntax
# and would corrupt the value silently.
TEMPLATE_PATH="$TEMPLATE" python3 <<'PY'
import os

placeholders = [
    "LOCATION", "GROUP_NAME", "DNS_LABEL", "ACR_SERVER", "ACR_USERNAME",
    "ACR_PASSWORD", "TAG", "MONGO_URI", "DEPLOY_SOURCE", "FQDN",
    "STORAGE_ACCOUNT", "STORAGE_KEY",
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
