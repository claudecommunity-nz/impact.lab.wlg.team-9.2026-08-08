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
#   * a Cosmos DB account (MongoDB API) + the `signals` database
#   * an Entra app registration with GitHub OIDC federation, so the workflow
#     authenticates with no stored password
#   * a Contributor role assignment scoped to the resource group only
#
# and then writes the resulting IDs into the GitHub repo as secrets and
# variables, if the `gh` CLI is available and authenticated.
#
# Safe to re-run: every step checks for what it already created.

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
ENV_FILE="$REPO_ROOT/deploy/azure/.azure-env"

LOCATION="${LOCATION:-australiaeast}"          # closest region to Wellington
RESOURCE_GROUP="${RESOURCE_GROUP:-team9-signals-rg}"
GROUP_NAME="${GROUP_NAME:-team9-signals}"
COSMOS_SERVER_VERSION="${COSMOS_SERVER_VERSION:-4.2}"
APP_NAME="${APP_NAME:-team9-signals-github-deploy}"

say()  { printf '\n\033[1m▸ %s\033[0m\n' "$*"; }
note() { printf '  %s\n' "$*"; }
warn() { printf '\033[33m  ! %s\033[0m\n' "$*"; }
fail() { printf '\n\033[31m✗ %s\033[0m\n' "$*" >&2; exit 1; }

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
read -r -p "Is that the right account? Type yes to continue: " confirm
[[ "$confirm" == "yes" ]] || fail "stopped. Run 'az login' as the hosting account, or 'az account set --subscription <id>'."

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
  SUFFIX="$(head -c 4 /dev/urandom | od -An -tx1 | tr -d ' \n')"
  ACR_NAME="team9signals${SUFFIX}"
  DNS_LABEL="team9-signals-${SUFFIX}"
  COSMOS_ACCOUNT="team9-signals-${SUFFIX}"
fi

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
# 3. Cosmos DB (MongoDB API)
# --------------------------------------------------------------------------
say "Cosmos DB account $COSMOS_ACCOUNT"
if az cosmosdb show --name "$COSMOS_ACCOUNT" --resource-group "$RESOURCE_GROUP" >/dev/null 2>&1; then
  note "already exists"
else
  # Free tier gives 1000 RU/s and 25 GB at no cost, but only one account per
  # subscription may have it. If it is already spoken for, fall back to
  # serverless, which bills per request and costs cents at this volume.
  USED_FREE_TIER="$(az cosmosdb list --query "[?enableFreeTier] | length(@)" -o tsv 2>/dev/null || echo 0)"

  if [[ "$USED_FREE_TIER" == "0" ]]; then
    note "no free-tier account in this subscription yet — using it"
    COSMOS_MODE="free"
    EXTRA_ARGS=(--enable-free-tier true)
  else
    warn "free tier already used by another account in this subscription — using serverless"
    COSMOS_MODE="serverless"
    EXTRA_ARGS=(--capabilities EnableServerless)
  fi

  note "creating (this takes 5-10 minutes)"
  if ! az cosmosdb create \
    --name "$COSMOS_ACCOUNT" \
    --resource-group "$RESOURCE_GROUP" \
    --kind MongoDB \
    --server-version "$COSMOS_SERVER_VERSION" \
    --default-consistency-level Session \
    --locations regionName="$LOCATION" failoverPriority=0 isZoneRedundant=False \
    "${EXTRA_ARGS[@]}" \
    --output none
  then
    if [[ "$COSMOS_MODE" == "free" ]]; then
      warn "free-tier creation failed; retrying as serverless"
      COSMOS_MODE="serverless"
      az cosmosdb create \
        --name "$COSMOS_ACCOUNT" \
        --resource-group "$RESOURCE_GROUP" \
        --kind MongoDB \
        --server-version "$COSMOS_SERVER_VERSION" \
        --default-consistency-level Session \
        --locations regionName="$LOCATION" failoverPriority=0 isZoneRedundant=False \
        --capabilities EnableServerless \
        --output none
    else
      fail "could not create the Cosmos DB account"
    fi
  fi
  note "created ($COSMOS_MODE)"
fi

say "Cosmos database 'signals'"
if az cosmosdb mongodb database show \
     --account-name "$COSMOS_ACCOUNT" --resource-group "$RESOURCE_GROUP" \
     --name signals >/dev/null 2>&1; then
  note "already exists"
else
  # Shared database-level throughput on a free-tier account: both collections
  # draw from the same 1000 RU/s, which is the part that is free. Per-collection
  # throughput would provision 400 RU/s each and start costing money.
  IS_SERVERLESS="$(az cosmosdb show --name "$COSMOS_ACCOUNT" --resource-group "$RESOURCE_GROUP" \
    --query "contains(to_string(capabilities), 'EnableServerless')" -o tsv 2>/dev/null || echo false)"

  if [[ "$IS_SERVERLESS" == "true" ]]; then
    az cosmosdb mongodb database create \
      --account-name "$COSMOS_ACCOUNT" --resource-group "$RESOURCE_GROUP" \
      --name signals --output none
    note "created (serverless — no provisioned throughput)"
  else
    az cosmosdb mongodb database create \
      --account-name "$COSMOS_ACCOUNT" --resource-group "$RESOURCE_GROUP" \
      --name signals --throughput 1000 --output none
    note "created (1000 RU/s shared across collections)"
  fi
fi

# --------------------------------------------------------------------------
# 4. GitHub OIDC federation — no stored password anywhere
# --------------------------------------------------------------------------
REPO_SLUG="${REPO_SLUG:-$(git -C "$REPO_ROOT" remote get-url origin \
  | sed -E 's#^.*[:/]([^/:]+/[^/]+?)(\.git)?$#\1#')}"
[[ "$REPO_SLUG" == */* ]] || fail "could not work out the GitHub repo from the git remote; set REPO_SLUG=owner/repo"

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

add_federated_credential "github-main" "repo:${REPO_SLUG}:ref:refs/heads/main"
add_federated_credential "github-env-production" "repo:${REPO_SLUG}:environment:production"

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
# 5. Save state and hand the values to GitHub
# --------------------------------------------------------------------------
cat > "$ENV_FILE" <<EOF
# Generated by bootstrap.sh — delete to start a fresh deployment.
RESOURCE_GROUP=$RESOURCE_GROUP
ACR_NAME=$ACR_NAME
GROUP_NAME=$GROUP_NAME
DNS_LABEL=$DNS_LABEL
COSMOS_ACCOUNT=$COSMOS_ACCOUNT
LOCATION=$LOCATION
EOF

say "GitHub configuration"
if command -v gh >/dev/null 2>&1 && gh auth status >/dev/null 2>&1; then
  gh secret set AZURE_CLIENT_ID       --repo "$REPO_SLUG" --body "$APP_ID"  >/dev/null
  gh secret set AZURE_TENANT_ID       --repo "$REPO_SLUG" --body "$TENANT_ID" >/dev/null
  gh secret set AZURE_SUBSCRIPTION_ID --repo "$REPO_SLUG" --body "$SUB_ID"  >/dev/null

  gh variable set AZURE_RESOURCE_GROUP --repo "$REPO_SLUG" --body "$RESOURCE_GROUP" >/dev/null
  gh variable set AZURE_ACR_NAME       --repo "$REPO_SLUG" --body "$ACR_NAME"       >/dev/null
  gh variable set AZURE_LOCATION       --repo "$REPO_SLUG" --body "$LOCATION"       >/dev/null
  gh variable set ACI_GROUP_NAME       --repo "$REPO_SLUG" --body "$GROUP_NAME"     >/dev/null
  gh variable set ACI_DNS_LABEL        --repo "$REPO_SLUG" --body "$DNS_LABEL"      >/dev/null
  gh variable set COSMOS_ACCOUNT       --repo "$REPO_SLUG" --body "$COSMOS_ACCOUNT" >/dev/null
  note "secrets and variables set on $REPO_SLUG"
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
    COSMOS_ACCOUNT         $COSMOS_ACCOUNT
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
