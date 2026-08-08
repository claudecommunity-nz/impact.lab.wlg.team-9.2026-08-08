#!/usr/bin/env bash
#
# One-time Azure setup. Run this once, by hand, against the Azure account that
# will host the deployment. After it finishes, pushes to main deploy themselves.
#
#   az login                        # the hosting account, not necessarily your usual one
#   ./deploy/azure/bootstrap.sh
#
# It creates:
#   * a resource group
#   * a container registry
#   * an Entra app registration with GitHub OIDC federation, so the workflow
#     authenticates with no stored password
#   * a Contributor role assignment scoped to the resource group only
#
# and collects the MongoDB Atlas connection string (the database lives outside
# Azure), then writes everything into the GitHub repo as secrets and variables
# if the `gh` CLI is available and authenticated.
#
# Safe to re-run: every step checks for what it already created.

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
ENV_FILE="$REPO_ROOT/deploy/azure/.azure-env"

LOCATION="${LOCATION:-australiaeast}"          # closest region to Wellington
RESOURCE_GROUP="${RESOURCE_GROUP:-team9-signals-rg}"
GROUP_NAME="${GROUP_NAME:-team9-signals}"
APP_NAME="${APP_NAME:-team9-signals-github-deploy}"
SECRET_NAME="${SECRET_NAME:-mongo-uri}"

say()  { printf '\n\033[1m▸ %s\033[0m\n' "$*"; }
note() { printf '  %s\n' "$*"; }
warn() { printf '\033[33m  ! %s\033[0m\n' "$*"; }
fail() { printf '\n\033[31m✗ %s\033[0m\n' "$*" >&2; exit 1; }

# --------------------------------------------------------------------------
# This script asks for a secret, so it wants a real terminal. Without one,
# `read` hits EOF and `set -e` exits with no explanation at all — which looks
# like a crash rather than a missing prompt. Say what happened instead.
# --------------------------------------------------------------------------
NONINTERACTIVE="${BOOTSTRAP_NONINTERACTIVE:-0}"

if [[ ! -t 0 && "$NONINTERACTIVE" != "1" ]]; then
  cat >&2 <<'EOF'

✗ No terminal attached, so this cannot prompt you.

  Run it from a terminal window:

      cd <repo>
      ./deploy/azure/bootstrap.sh

  That matters here beyond convenience: the script asks for the Atlas
  connection string, and a real terminal reads it hidden and keeps it out of
  your shell history.

  To run it unattended anyway (CI, or a wrapper script):

      BOOTSTRAP_NONINTERACTIVE=1 MONGO_URI='mongodb+srv://...' ./deploy/azure/bootstrap.sh

  That form takes the connection string from the environment and skips every
  confirmation, so make sure the account below is the one you want billed.

EOF
  exit 1
fi

if [[ "$NONINTERACTIVE" == "1" && -z "${MONGO_URI:-}" ]]; then
  fail "BOOTSTRAP_NONINTERACTIVE=1 needs MONGO_URI set in the environment"
fi

# --------------------------------------------------------------------------
# 0. Confirm we are pointed at the right Azure account
# --------------------------------------------------------------------------
az account show >/dev/null 2>&1 || fail "not logged in — run: az login"

SUB_ID="$(az account show --query id -o tsv)"
SUB_NAME="$(az account show --query name -o tsv)"
TENANT_ID="$(az account show --query tenantId -o tsv)"
ACCOUNT_USER="$(az account show --query user.name -o tsv)"

cat <<EOF

This will create billable resources in:

  Account       $ACCOUNT_USER
  Subscription  $SUB_NAME
  ID            $SUB_ID
  Tenant        $TENANT_ID
  Region        $LOCATION

EOF

# Asked explicitly because the hosting account is often not the one the machine
# is normally signed in to, and az silently uses whichever was last used.
if [[ "$NONINTERACTIVE" != "1" ]]; then
  read -r -p "Is that the right account? Type yes to continue: " confirm \
    || fail "no input received"
  [[ "$confirm" == "yes" ]] \
    || fail "stopped. Run 'az login' as the hosting account, or 'az account set --subscription <id>'."
else
  note "non-interactive: proceeding with the account above"
fi

# --------------------------------------------------------------------------
# Work out the GitHub repo now, before anything is created. This used to sit
# further down and a failure here left billable resources behind.
#
# Parameter expansion rather than sed: the obvious regex needs a non-greedy
# quantifier, which is not POSIX ERE — GNU sed tolerates `+?`, BSD sed on macOS
# rejects it outright. This handles every remote form we care about, including
# the SSH-alias one (git@github-team9:owner/repo.git).
# --------------------------------------------------------------------------
if [[ -z "${REPO_SLUG:-}" ]]; then
  remote_url="$(git -C "$REPO_ROOT" remote get-url origin 2>/dev/null || true)"
  [[ -n "$remote_url" ]] || fail "no 'origin' remote here; set REPO_SLUG=owner/repo"

  slug_path="${remote_url%.git}"      # trailing .git only — repo names contain dots
  slug_path="${slug_path%/}"
  slug_repo="${slug_path##*/}"        # last path segment
  slug_rest="${slug_path%/*}"         # everything before it
  slug_owner="${slug_rest##*[:/]}"    # segment after the last : or /
  REPO_SLUG="$slug_owner/$slug_repo"
fi
[[ "$REPO_SLUG" == */* && "$REPO_SLUG" != */*/* ]] \
  || fail "could not work out the GitHub repo from '${remote_url:-}' — set REPO_SLUG=owner/repo"

# --------------------------------------------------------------------------
# 1. Resource group
# --------------------------------------------------------------------------
say "Resource group $RESOURCE_GROUP"
az group create --name "$RESOURCE_GROUP" --location "$LOCATION" --output none
note "ready"

# --------------------------------------------------------------------------
# 2. Container registry — name must be globally unique
# --------------------------------------------------------------------------
if [[ -f "$ENV_FILE" ]]; then
  # shellcheck disable=SC1090
  source "$ENV_FILE"
fi

if [[ -z "${ACR_NAME:-}" ]]; then
  # An earlier run may have created the registry and then failed before it
  # could save the names. Adopt what is already in the resource group rather
  # than minting a fresh suffix and building a second set of everything.
  EXISTING_ACR="$(az acr list --resource-group "$RESOURCE_GROUP" \
    --query "[?starts_with(name, 'team9signals')].name | [0]" -o tsv 2>/dev/null || true)"

  if [[ -n "$EXISTING_ACR" && "$EXISTING_ACR" != "None" ]]; then
    ACR_NAME="$EXISTING_ACR"
    SUFFIX="${ACR_NAME#team9signals}"
    note "adopting the registry an earlier run left behind: $ACR_NAME"
  else
    SUFFIX="$(head -c 4 /dev/urandom | od -An -tx1 | tr -d ' \n')"
    ACR_NAME="team9signals${SUFFIX}"
  fi

  DNS_LABEL="team9-signals-${SUFFIX}"
  # Vault names are globally unique and max 24 characters. The random suffix
  # also sidesteps Key Vault soft-delete: a deleted vault holds its name for 90
  # days, so a re-bootstrap with a fixed name would collide with its own ghost.
  KEY_VAULT_NAME="team9-kv-${SUFFIX}"
  # Storage account names: globally unique, 3-24 chars, lowercase alphanumeric
  # only — no hyphens, which is why this one does not match the pattern above.
  STORAGE_ACCOUNT="team9st${SUFFIX}"
fi

# Written now, not at the end. Saving names only once everything succeeded is
# what turns one failed run into two of every resource.
save_env() {
  cat > "$ENV_FILE" <<EOF
# Generated by bootstrap.sh — delete to start a fresh deployment.
# Names only. The connection string is in Key Vault, not here.
RESOURCE_GROUP=$RESOURCE_GROUP
ACR_NAME=$ACR_NAME
GROUP_NAME=$GROUP_NAME
DNS_LABEL=$DNS_LABEL
KEY_VAULT_NAME=$KEY_VAULT_NAME
SECRET_NAME=$SECRET_NAME
STORAGE_ACCOUNT=$STORAGE_ACCOUNT
LOCATION=$LOCATION
EOF
}
save_env

say "Container registry $ACR_NAME"
if az acr show --name "$ACR_NAME" --resource-group "$RESOURCE_GROUP" >/dev/null 2>&1; then
  note "already exists"
else
  az acr create \
    --resource-group "$RESOURCE_GROUP" \
    --name "$ACR_NAME" \
    --sku Basic \
    --admin-enabled true \
    --output none
  note "created"
fi

# --------------------------------------------------------------------------
# 2b. Storage for Caddy's TLS certificates
# --------------------------------------------------------------------------
# Every deploy deletes and recreates the container group, so without somewhere
# durable to keep them Caddy would request a fresh certificate each time. Let's
# Encrypt permits five duplicate certificates per week; a day of iterating
# would burn through that and leave the demo showing a rate-limit error.
#
# Unlike the database, this is a genuinely fine use of Azure Files: Caddy wants
# an ordinary filesystem, not the locking semantics SMB cannot provide.
say "Storage account $STORAGE_ACCOUNT (TLS certificate persistence)"
if az storage account show --name "$STORAGE_ACCOUNT" --resource-group "$RESOURCE_GROUP" >/dev/null 2>&1; then
  note "already exists"
else
  az storage account create \
    --name "$STORAGE_ACCOUNT" \
    --resource-group "$RESOURCE_GROUP" \
    --location "$LOCATION" \
    --sku Standard_LRS \
    --kind StorageV2 \
    --min-tls-version TLS1_2 \
    --allow-blob-public-access false \
    --output none
  note "created"
fi

STORAGE_KEY="$(az storage account keys list \
  --account-name "$STORAGE_ACCOUNT" --resource-group "$RESOURCE_GROUP" \
  --query '[0].value' -o tsv)"

if az storage share exists --name caddy-data \
     --account-name "$STORAGE_ACCOUNT" --account-key "$STORAGE_KEY" \
     --query exists -o tsv 2>/dev/null | grep -q true; then
  note "file share 'caddy-data' already exists"
else
  az storage share create --name caddy-data \
    --account-name "$STORAGE_ACCOUNT" --account-key "$STORAGE_KEY" \
    --quota 1 --output none
  note "file share 'caddy-data' created (1 GiB)"
fi

# --------------------------------------------------------------------------
# 3. MongoDB Atlas connection string
# --------------------------------------------------------------------------
# The database is a free Atlas M0 cluster rather than anything in Azure. It is
# real MongoDB, which is what this pipeline was built and tested against — a
# compatibility layer would mean finding out about its differences on the day.
# Creating the cluster is a web signup, so it is the one thing here that cannot
# be scripted; this step collects the result and checks it works.
say "MongoDB Atlas connection string"

# Already in the vault from a previous run? Reuse it. Re-running this script is
# a normal thing to do — adding a resource, repairing a role assignment — and
# it should not mean hunting down the connection string again every time.
if [[ -z "${MONGO_URI:-}" && -n "${KEY_VAULT_NAME:-}" ]]; then
  if EXISTING="$(az keyvault secret show --vault-name "$KEY_VAULT_NAME" \
       --name "$SECRET_NAME" --query value -o tsv 2>/dev/null)" && [[ -n "$EXISTING" ]]; then
    MONGO_URI="$EXISTING"
    REUSED_SECRET=1
    note "reusing the connection string already in $KEY_VAULT_NAME"
  fi
fi

if [[ -z "${MONGO_URI:-}" ]]; then
  cat <<'EOF'

  From the Atlas UI: Database → Connect → Drivers → Python, and copy the
  connection string. Then replace <password> with the database user's actual
  password. It looks like:

    mongodb+srv://USER:PASSWORD@cluster0.xxxxx.mongodb.net/?retryWrites=true&w=majority

EOF
  read -r -s -p "  Paste it (hidden): " MONGO_URI || fail "no input received"
  echo
fi

[[ -n "$MONGO_URI" ]] || fail "no connection string given"
[[ "$MONGO_URI" == mongodb+srv://* || "$MONGO_URI" == mongodb://* ]] \
  || fail "that does not look like a MongoDB connection string"

if [[ "$MONGO_URI" == *"<password>"* || "$MONGO_URI" == *"<db_password>"* ]]; then
  fail "the placeholder is still in there — replace <password> with the real one"
fi

# Verified now rather than at deploy time. A wrong password or a missing
# network-access entry produces an identical symptom half an hour later —
# containers that start, then sit there failing to reach the database.
if [[ "${REUSED_SECRET:-}" == "1" ]]; then
  note "skipping the connection test — this string was already working"
elif command -v docker >/dev/null 2>&1 && docker info >/dev/null 2>&1; then
  note "testing the connection"
  if docker run --rm mongo:7 mongosh "$MONGO_URI" --quiet \
       --eval 'db.adminCommand({ping:1}).ok' 2>/dev/null | grep -q 1; then
    note "connected"
  else
    warn "could not connect. The usual causes, in order of likelihood:"
    warn "  1. Network Access in Atlas does not allow 0.0.0.0/0"
    warn "  2. the password is wrong, or has unescaped special characters"
    warn "  3. the database user has no read/write role"
    # Fails closed when unattended. Storing a connection string that does not
    # work only moves the failure to the smoke test, twenty minutes later.
    [[ "$NONINTERACTIVE" != "1" ]] \
      || fail "connection test failed — refusing to store a string that does not work"
    read -r -p "  Continue anyway? Type yes: " ignore_conn || fail "no input received"
    [[ "$ignore_conn" == "yes" ]] || fail "stopped — fix the connection and re-run"
  fi
else
  warn "docker not available, skipping the connection test"
fi

note "held in memory — it goes into Key Vault below, not onto disk"

# --------------------------------------------------------------------------
# 4. GitHub OIDC federation — no stored password anywhere
# --------------------------------------------------------------------------
say "Entra app registration $APP_NAME (for repo $REPO_SLUG)"
APP_ID="$(az ad app list --display-name "$APP_NAME" --query "[0].appId" -o tsv 2>/dev/null || true)"

if [[ -z "$APP_ID" || "$APP_ID" == "None" ]]; then
  APP_ID="$(az ad app create --display-name "$APP_NAME" --query appId -o tsv)" \
    || fail "could not create the app registration — your account may not be allowed to. See README.md."
  note "created app $APP_ID"
else
  note "already exists ($APP_ID)"
fi

if ! az ad sp show --id "$APP_ID" >/dev/null 2>&1; then
  az ad sp create --id "$APP_ID" --output none
  note "service principal created"
fi
SP_OBJECT_ID="$(az ad sp show --id "$APP_ID" --query id -o tsv)"

# One federated credential per trusted GitHub context. The subject must match
# exactly what the workflow's token will claim, or login fails with an
# unhelpful AADSTS70021.
add_federated_credential() {
  local name="$1" subject="$2"
  if az ad app federated-credential show --id "$APP_ID" --federated-credential-id "$name" >/dev/null 2>&1; then
    note "federated credential '$name' already exists"
    return
  fi
  az ad app federated-credential create --id "$APP_ID" --parameters "$(cat <<EOF
{
  "name": "$name",
  "issuer": "https://token.actions.githubusercontent.com",
  "subject": "$subject",
  "audiences": ["api://AzureADTokenExchange"]
}
EOF
)" --output none
  note "federated credential '$name' created"
}

# GitHub can present either of two subject forms, and which one you get is an
# organisation setting rather than anything visible from here:
#
#   repo:owner/repo:ref:refs/heads/main
#   repo:owner@<owner-id>/repo@<repo-id>:ref:refs/heads/main
#
# The second is the immutable form, which pins the identity to numeric IDs so a
# rename cannot be used to impersonate a repo. A credential for the wrong form
# fails with AADSTS700213 and a subject you have to read very carefully to spot
# the difference. Registering both costs nothing and covers the setting being
# on, off, or changed later.
add_federated_credential "github-main" "repo:${REPO_SLUG}:ref:refs/heads/main"
add_federated_credential "github-env-production" "repo:${REPO_SLUG}:environment:production"

OWNER_ID=""; REPO_ID=""
if command -v gh >/dev/null 2>&1 && gh auth status >/dev/null 2>&1; then
  OWNER_ID="$(gh api "/repos/$REPO_SLUG" --jq '.owner.id' 2>/dev/null || true)"
  REPO_ID="$(gh api "/repos/$REPO_SLUG" --jq '.id' 2>/dev/null || true)"
fi

if [[ -n "$OWNER_ID" && -n "$REPO_ID" ]]; then
  SLUG_WITH_IDS="${REPO_SLUG%%/*}@${OWNER_ID}/${REPO_SLUG#*/}@${REPO_ID}"
  add_federated_credential "github-main-ids" "repo:${SLUG_WITH_IDS}:ref:refs/heads/main"
  add_federated_credential "github-env-production-ids" "repo:${SLUG_WITH_IDS}:environment:production"
else
  warn "could not read the numeric repo/owner IDs from GitHub"
  warn "if the deploy fails with AADSTS700213, add a federated credential whose"
  warn "subject matches the one printed in the workflow log, exactly"
fi

say "Role assignment (Contributor, scoped to $RESOURCE_GROUP only)"
if az role assignment list \
     --assignee "$SP_OBJECT_ID" \
     --scope "/subscriptions/$SUB_ID/resourceGroups/$RESOURCE_GROUP" \
     --query "[?roleDefinitionName=='Contributor'] | length(@)" -o tsv 2>/dev/null | grep -q '^[1-9]'; then
  note "already assigned"
else
  # Retry: a freshly created service principal is not immediately visible to
  # the role assignment API.
  for attempt in 1 2 3 4 5; do
    if az role assignment create \
         --assignee-object-id "$SP_OBJECT_ID" \
         --assignee-principal-type ServicePrincipal \
         --role Contributor \
         --scope "/subscriptions/$SUB_ID/resourceGroups/$RESOURCE_GROUP" \
         --output none 2>/dev/null; then
      note "assigned"
      break
    fi
    note "waiting for the service principal to propagate (attempt $attempt)"
    sleep 10
  done
fi

# --------------------------------------------------------------------------
# 5. Key Vault — the one place the connection string lives
# --------------------------------------------------------------------------
# Why bother, when the deploy pipeline could just as easily read a GitHub
# secret: repo secrets are available to workflows on *any* branch, so anyone
# with push access can write a workflow that prints them. The OIDC federation
# is scoped to refs/heads/main, so a secret behind the vault needs a merge to
# main to reach. It is also the rotation point and the audit trail.
#
# What it does not do: ACI has no native Key Vault reference for environment
# variables, so the value is still injected into the container as a
# secureValue. The vault is the source of truth, not a way to hide the value
# from the running process.
say "Key Vault $KEY_VAULT_NAME"

if az keyvault show --name "$KEY_VAULT_NAME" --resource-group "$RESOURCE_GROUP" >/dev/null 2>&1; then
  note "already exists"
else
  # RBAC rather than the older access-policy model: it is the current default,
  # and it means permissions are visible in the same place as everything else.
  az keyvault create \
    --name "$KEY_VAULT_NAME" \
    --resource-group "$RESOURCE_GROUP" \
    --location "$LOCATION" \
    --enable-rbac-authorization true \
    --output none
  note "created"
fi

VAULT_SCOPE="/subscriptions/$SUB_ID/resourceGroups/$RESOURCE_GROUP/providers/Microsoft.KeyVault/vaults/$KEY_VAULT_NAME"

# The person running this needs write access to put the secret in. Creating a
# vault does not grant it — under RBAC, being the vault's creator gives you
# nothing on its contents.
say "Granting yourself write access to the vault"
ME_OBJECT_ID="$(az ad signed-in-user show --query id -o tsv 2>/dev/null || true)"
if [[ -n "$ME_OBJECT_ID" ]]; then
  if az role assignment list --assignee "$ME_OBJECT_ID" --scope "$VAULT_SCOPE" \
       --query "[?roleDefinitionName=='Key Vault Secrets Officer'] | length(@)" -o tsv 2>/dev/null | grep -q '^[1-9]'; then
    note "already granted"
  else
    az role assignment create \
      --assignee-object-id "$ME_OBJECT_ID" \
      --assignee-principal-type User \
      --role "Key Vault Secrets Officer" \
      --scope "$VAULT_SCOPE" \
      --output none
    note "granted"
  fi
else
  warn "could not identify the signed-in user (are you logged in as a service principal?)"
  warn "grant yourself 'Key Vault Secrets Officer' on $KEY_VAULT_NAME manually if the next step fails"
fi

say "Granting the deploy identity read access to the vault"
if az role assignment list --assignee "$SP_OBJECT_ID" --scope "$VAULT_SCOPE" \
     --query "[?roleDefinitionName=='Key Vault Secrets User'] | length(@)" -o tsv 2>/dev/null | grep -q '^[1-9]'; then
  note "already granted"
else
  az role assignment create \
    --assignee-object-id "$SP_OBJECT_ID" \
    --assignee-principal-type ServicePrincipal \
    --role "Key Vault Secrets User" \
    --scope "$VAULT_SCOPE" \
    --output none
  note "granted (read-only — the workflow can read secrets, not write them)"
fi

say "Storing the connection string as '$SECRET_NAME'"
# RBAC assignments take a little while to reach the data plane. Writing
# immediately after granting reliably fails with 403 the first time or two,
# which looks like a permissions mistake and is not.
for attempt in 1 2 3 4 5 6; do
  if az keyvault secret set \
       --vault-name "$KEY_VAULT_NAME" \
       --name "$SECRET_NAME" \
       --value "$MONGO_URI" \
       --output none 2>/dev/null; then
    note "stored"
    break
  fi
  if [[ $attempt -eq 6 ]]; then
    fail "could not write the secret — check you have 'Key Vault Secrets Officer' on $KEY_VAULT_NAME"
  fi
  note "waiting for the role assignment to reach the data plane (attempt $attempt)"
  sleep 10
done

# --------------------------------------------------------------------------
# 6. Save state and hand the values to GitHub
# --------------------------------------------------------------------------
save_env

# Nothing below is a credential. The three "secrets" are Azure identifiers —
# they are secrets by convention, and useless without the OIDC federation,
# which is bound to this repo's main branch.
say "GitHub configuration"
if command -v gh >/dev/null 2>&1 && gh auth status >/dev/null 2>&1; then
  gh secret set AZURE_CLIENT_ID       --repo "$REPO_SLUG" --body "$APP_ID"    >/dev/null
  gh secret set AZURE_TENANT_ID       --repo "$REPO_SLUG" --body "$TENANT_ID" >/dev/null
  gh secret set AZURE_SUBSCRIPTION_ID --repo "$REPO_SLUG" --body "$SUB_ID"    >/dev/null

  gh variable set AZURE_RESOURCE_GROUP --repo "$REPO_SLUG" --body "$RESOURCE_GROUP" >/dev/null
  gh variable set AZURE_ACR_NAME       --repo "$REPO_SLUG" --body "$ACR_NAME"       >/dev/null
  gh variable set AZURE_LOCATION       --repo "$REPO_SLUG" --body "$LOCATION"       >/dev/null
  gh variable set ACI_GROUP_NAME       --repo "$REPO_SLUG" --body "$GROUP_NAME"     >/dev/null
  gh variable set ACI_DNS_LABEL        --repo "$REPO_SLUG" --body "$DNS_LABEL"      >/dev/null
  gh variable set AZURE_KEY_VAULT_NAME --repo "$REPO_SLUG" --body "$KEY_VAULT_NAME" >/dev/null
  gh variable set MONGO_SECRET_NAME    --repo "$REPO_SLUG" --body "$SECRET_NAME"    >/dev/null
  gh variable set AZURE_STORAGE_ACCOUNT --repo "$REPO_SLUG" --body "$STORAGE_ACCOUNT" >/dev/null
  note "secrets and variables set on $REPO_SLUG"

  # Left over from the previous design, where the connection string was a repo
  # secret. Remove it so there is exactly one copy, in the vault.
  if gh secret list --repo "$REPO_SLUG" 2>/dev/null | grep -q '^MONGO_URI'; then
    gh secret delete MONGO_URI --repo "$REPO_SLUG" >/dev/null 2>&1 \
      && note "removed the old MONGO_URI repo secret — it lives in Key Vault now"
  fi
else
  warn "gh CLI not available or not authenticated — set these by hand:"
  cat <<EOF

  Repository secrets (Settings → Secrets and variables → Actions → Secrets):
    AZURE_CLIENT_ID        $APP_ID
    AZURE_TENANT_ID        $TENANT_ID
    AZURE_SUBSCRIPTION_ID  $SUB_ID

  Repository variables (same page → Variables):
    AZURE_RESOURCE_GROUP   $RESOURCE_GROUP
    AZURE_ACR_NAME         $ACR_NAME
    AZURE_LOCATION         $LOCATION
    ACI_GROUP_NAME         $GROUP_NAME
    ACI_DNS_LABEL          $DNS_LABEL
    AZURE_KEY_VAULT_NAME   $KEY_VAULT_NAME
    MONGO_SECRET_NAME      $SECRET_NAME
    AZURE_STORAGE_ACCOUNT  $STORAGE_ACCOUNT

  There is deliberately no MONGO_URI secret — delete it if one is still there.
EOF
fi

cat <<EOF

$(printf '\033[1m▸ Done.\033[0m')

  Deploy now:   gh workflow run deploy-azure.yml
  Or:           push anything to main

  The app will come up at:
    http://${DNS_LABEL}.${LOCATION}.azurecontainer.io/
    http://${DNS_LABEL}.${LOCATION}.azurecontainer.io:8000/stats

  Still worth doing by hand: set a spending budget with an alert.
  Portal → Cost Management → Budgets → Add. See deploy/azure/README.md.

EOF

unset MONGO_URI
