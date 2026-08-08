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

# Optional. An absent credential must not block a deploy — the collector that
# needs it reports itself as skipped on the dashboard, which is visible and
# recoverable, whereas a failed deploy takes the whole site with it.
export REDDIT_API_KEY="${REDDIT_API_KEY:-}"
export SIM_ANCHOR="${SIM_ANCHOR:-2026-04-20T00:00:00Z}"
export SIM_REAL_ANCHOR="${SIM_REAL_ANCHOR:-2026-08-08T00:00:00Z}"
export ANTHROPIC_API_KEY="${ANTHROPIC_API_KEY:-}"
export SCREENSHOT_BLOB_URL="${SCREENSHOT_BLOB_URL:-}"
export SCREENSHOT_BLOB_SAS="${SCREENSHOT_BLOB_SAS:-}"

# Substituted in Python, not sed: a MongoDB connection string routinely
# contains & and /, both of which mean something to sed's replacement syntax
# and would corrupt the value silently.
TEMPLATE_PATH="$TEMPLATE" python3 <<'PY'
import os
import re

placeholders = [
    "LOCATION", "GROUP_NAME", "DNS_LABEL", "ACR_SERVER", "ACR_USERNAME",
    "ACR_PASSWORD", "TAG", "MONGO_URI", "DEPLOY_SOURCE", "FQDN",
    "STORAGE_ACCOUNT", "STORAGE_KEY", "REDDIT_API_KEY",
    "SIM_ANCHOR", "SIM_REAL_ANCHOR",
    "ANTHROPIC_API_KEY", "SCREENSHOT_BLOB_URL", "SCREENSHOT_BLOB_SAS",
]

with open(os.environ["TEMPLATE_PATH"]) as fh:
    text = fh.read()

# Blocks between #<<<NAME and #>>>NAME are dropped when the feature they need
# is not configured, rather than rendered with empty values. ACI's handling of
# an empty secureValue is not something to find out about four minutes before
# a demo, and a collector with no key could only idle anyway.
#
# Resolved before the quoting check below, so a dropped block's placeholders
# are simply not there to check.
def strip_optional_block(text: str, name: str, keep: bool) -> str:
    pattern = re.compile(
        rf"^[ \t]*#<<<{name}\n(?P<body>.*?)^[ \t]*#>>>{name}\n",
        re.DOTALL | re.MULTILINE,
    )
    return pattern.sub((lambda m: m.group("body")) if keep else "", text)


text = strip_optional_block(
    text,
    "SCREENSHOTS",
    bool(
        os.environ["ANTHROPIC_API_KEY"]
        and os.environ["SCREENSHOT_BLOB_URL"]
        and os.environ["SCREENSHOT_BLOB_SAS"]
    ),
)

# Every placeholder must sit inside double quotes. Checked here because the
# symptom of getting it wrong is not a YAML error — it is a value that parses
# as the wrong type and comes back from Azure as a SerializationError with a
# thousand-line dump. Cheaper to fail on the template.
unquoted = []
for lineno, line in enumerate(text.split("\n"), 1):
    stripped = line.strip()
    if "__" not in stripped or stripped.startswith("#"):
        continue
    for match in re.finditer(r"__[A-Z_]+__", line):
        before = line[: match.start()]
        after = line[match.end():]
        if before.count('"') % 2 == 0 or '"' not in after:
            unquoted.append(f"  line {lineno}: {stripped}")
            break
if unquoted:
    raise SystemExit(
        "render-aci.sh: these placeholders are not inside double quotes —\n"
        + "\n".join(unquoted)
        + "\n\nYAML types bare scalars: an ISO timestamp becomes a datetime, an\n"
          "all-digit git SHA becomes an int, and Azure rejects both."
    )


def yaml_escape(value: str) -> str:
    """Escape for a double-quoted YAML scalar.

    Every placeholder in the template sits inside double quotes, because YAML
    guesses types for bare scalars and guesses wrong in ways that surface as
    an unreadable serialization error from Azure rather than a YAML complaint:

      2026-04-20T00:00:00Z   -> a datetime, not a string
      1234567                -> an int, if a git SHA happens to be all digits
      no / yes / on          -> a bool

    Quoting removes the guessing. This only has to make the value safe inside
    those quotes — backslash and double-quote are the two characters that would
    otherwise end the scalar early, and a storage key or connection string can
    contain either.
    """
    return value.replace("\\", "\\\\").replace('"', '\\"')


for name in placeholders:
    text = text.replace(f"__{name}__", yaml_escape(os.environ[name]))

leftover = [p for p in placeholders if f"__{p}__" in text]
if leftover:
    raise SystemExit(f"render-aci.sh: unsubstituted placeholders: {leftover}")

print(text, end="")
PY
