#!/usr/bin/env bash
#
# Deploy from this machine. The usual path is the GitHub Actions workflow
# (.github/workflows/deploy-azure.yml) — this exists for when you want to push
# a change without committing it, or to debug the deployment itself.
#
#   ./deploy/azure/bootstrap.sh       once, first
#   ./deploy/azure/deploy.sh          build and deploy
#   ./deploy/azure/deploy.sh status   state and public URLs
#   ./deploy/azure/deploy.sh logs     recent logs
#   ./deploy/azure/deploy.sh secret <name>   store a secret in Key Vault
#   ./deploy/azure/deploy.sh secrets         list what is in the vault
#   ./deploy/azure/deploy.sh rotate          replace the database connection string
#   ./deploy/azure/deploy.sh stop     delete the container group, keep the data
#   ./deploy/azure/deploy.sh destroy  delete the Azure resources
#
# The connection string is read from Key Vault at deploy time and never written
# to disk. The database itself is a MongoDB Atlas cluster, outside Azure —
# nothing here can delete it; that is done in the Atlas UI.

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
ENV_FILE="$REPO_ROOT/deploy/azure/.azure-env"
# Left over from before the rendered file moved to a temp dir. Cleaned up by
# `destroy` so an old copy with live credentials doesn't linger.
STALE_FILES=(
  "$REPO_ROOT/deploy/azure/aci.generated.yaml"
  "$REPO_ROOT/deploy/azure/.mongo-uri"
)

say()  { printf '\n\033[1m▸ %s\033[0m\n' "$*"; }
fail() { printf '\n\033[31m✗ %s\033[0m\n' "$*" >&2; exit 1; }

az account show >/dev/null 2>&1 || fail "not logged in — run: az login"
[[ -f "$ENV_FILE" ]] || fail "no $ENV_FILE — run ./deploy/azure/bootstrap.sh first"
# shellcheck disable=SC1090
source "$ENV_FILE"

# An .azure-env written before the Key Vault change has no vault name in it.
# Say so plainly rather than failing later on an empty --vault-name.
[[ -n "${KEY_VAULT_NAME:-}" ]] \
  || fail "$ENV_FILE predates the Key Vault change — re-run ./deploy/azure/bootstrap.sh to add it"
[[ -n "${STORAGE_ACCOUNT:-}" ]] \
  || fail "$ENV_FILE predates the HTTPS change — re-run ./deploy/azure/bootstrap.sh to add the certificate storage"
SECRET_NAME="${SECRET_NAME:-mongo-uri}"
REDDIT_SECRET_NAME="${REDDIT_SECRET_NAME:-reddit-api-key}"

TAG="${TAG:-$(git -C "$REPO_ROOT" rev-parse --short HEAD 2>/dev/null || echo latest)}"

cmd_deploy() {
  say "Subscription: $(az account show --query name -o tsv)"
  say "Deploying tag $TAG to $GROUP_NAME in $RESOURCE_GROUP"

  for image in ingestion scrapers enrichment ui; do
    say "Building team9-$image (linux/amd64, in Azure)"
    az acr build \
      --registry "$ACR_NAME" \
      --image "team9-$image:$TAG" \
      --image "team9-$image:latest" \
      --platform linux/amd64 \
      --file "$REPO_ROOT/$image/Dockerfile" \
      "$REPO_ROOT/$image" \
      --output none
  done

  say "Rendering the container group definition"
  export LOCATION GROUP_NAME DNS_LABEL TAG
  export DEPLOY_SOURCE="local-$(whoami)"
  ACR_SERVER="$(az acr show --name "$ACR_NAME" --query loginServer -o tsv)"
  ACR_USERNAME="$(az acr credential show --name "$ACR_NAME" --query username -o tsv)"
  ACR_PASSWORD="$(az acr credential show --name "$ACR_NAME" --query 'passwords[0].value' -o tsv)"

  # Straight out of Key Vault. It is never written to disk here, and there is
  # no local copy to go stale after a rotation.
  if [[ -z "${MONGO_URI:-}" ]]; then
    MONGO_URI="$(az keyvault secret show \
      --vault-name "$KEY_VAULT_NAME" --name "$SECRET_NAME" \
      --query value -o tsv 2>/dev/null)" \
      || fail "could not read '$SECRET_NAME' from Key Vault '$KEY_VAULT_NAME'. You need the 'Key Vault Secrets User' role (or Officer) on the vault."
  fi
  STORAGE_KEY="$(az storage account keys list \
    --account-name "$STORAGE_ACCOUNT" --resource-group "$RESOURCE_GROUP" \
    --query '[0].value' -o tsv 2>/dev/null)" \
    || fail "could not read the storage key for '$STORAGE_ACCOUNT' — re-run bootstrap.sh"
  # Optional. Absent means the Reddit collector reports itself skipped on the
  # dashboard, which is visible and fixable — better than refusing to deploy.
  REDDIT_API_KEY="${REDDIT_API_KEY:-$(az keyvault secret show \
    --vault-name "$KEY_VAULT_NAME" --name "$REDDIT_SECRET_NAME" \
    --query value -o tsv 2>/dev/null || true)}"
  if [[ -z "$REDDIT_API_KEY" ]]; then
    say "No '$REDDIT_SECRET_NAME' in the vault — the Reddit collector will report as skipped"
    say "Add it with:  $0 secret $REDDIT_SECRET_NAME"
  fi

  export ACR_SERVER ACR_USERNAME ACR_PASSWORD MONGO_URI STORAGE_ACCOUNT STORAGE_KEY REDDIT_API_KEY

  # The rendered file carries the registry password and the connection string
  # in plaintext, so it is created 0600 in a temp dir and removed on the way
  # out — including if this script is interrupted mid-deploy. It used to sit in
  # the repo as aci.generated.yaml, one bad `git add -A` away from a leak.
  local workdir
  workdir="$(umask 077 && mktemp -d)"
  # shellcheck disable=SC2064 — expand workdir now, not at trap time
  trap "rm -rf '$workdir'" EXIT INT TERM
  local generated="$workdir/aci.yaml"

  ( umask 077 && "$REPO_ROOT/deploy/azure/render-aci.sh" > "$generated" )
  say "Rendered to a temporary 0600 file (removed when this exits)"

  # ACI cannot update a running group in place; replace it. The data is in
  # Atlas, outside the group, so this costs nothing but a minute of downtime.
  if az container show --resource-group "$RESOURCE_GROUP" --name "$GROUP_NAME" >/dev/null 2>&1; then
    say "Removing the previous container group"
    az container delete --resource-group "$RESOURCE_GROUP" --name "$GROUP_NAME" --yes --output none
  fi

  say "Creating container group $GROUP_NAME (a few minutes)"
  az container create --resource-group "$RESOURCE_GROUP" --file "$generated" --output none

  cmd_status
}

cmd_status() {
  local fqdn state
  fqdn="$(az container show --resource-group "$RESOURCE_GROUP" --name "$GROUP_NAME" \
    --query ipAddress.fqdn -o tsv 2>/dev/null || true)"
  state="$(az container show --resource-group "$RESOURCE_GROUP" --name "$GROUP_NAME" \
    --query instanceView.state -o tsv 2>/dev/null || echo "not deployed")"

  say "State: $state"
  [[ -n "$fqdn" ]] || return 0
  cat <<EOF

  UI        https://$fqdn/
  Signals   https://$fqdn/api/signals.geojson
  Groups    https://$fqdn/api/clusters.geojson
  API docs  https://$fqdn/api/docs

  Plain HTTP, still live in case TLS is having a bad day:
  UI        http://$fqdn:8080/
  API       http://$fqdn:8000/

EOF
}

cmd_logs() {
  local container="${1:-}"
  if [[ -z "$container" ]]; then
    for c in api scrapers enrichment; do
      say "--- $c ---"
      az container logs --resource-group "$RESOURCE_GROUP" --name "$GROUP_NAME" \
        --container-name "$c" --tail 25 || true
    done
  else
    az container logs --resource-group "$RESOURCE_GROUP" --name "$GROUP_NAME" \
      --container-name "$container" --follow
  fi
}

cmd_secret() {
  local name="${1:-$SECRET_NAME}"
  [[ -t 0 ]] || fail "no terminal attached — run this from a terminal window so the value stays out of your shell history"

  say "Setting '$name' in Key Vault $KEY_VAULT_NAME"
  echo "  Paste the value (input hidden)."
  read -r -s -p "  > " value || fail "no input received"
  echo
  [[ -n "$value" ]] || fail "nothing entered"

  # The connection string is checked before it is stored, because a wrong one
  # stays silent until containers are already up and failing. Other secrets
  # have nothing meaningful to test against and are taken at face value.
  if [[ "$name" == "$SECRET_NAME" ]]; then
    [[ "$value" == mongodb+srv://* || "$value" == mongodb://* ]] \
      || fail "that does not look like a MongoDB connection string"
    [[ "$value" != *"<password>"* ]] || fail "the <password> placeholder is still in there"
    if command -v docker >/dev/null 2>&1 && docker info >/dev/null 2>&1; then
      say "Testing it before storing"
      docker run --rm mongo:7 mongosh "$value" --quiet \
        --eval 'db.adminCommand({ping:1}).ok' 2>/dev/null | grep -q 1 \
        || fail "could not connect with that string — nothing was changed"
      say "Connected"
    fi
  fi

  az keyvault secret set --vault-name "$KEY_VAULT_NAME" --name "$name" \
    --value "$value" --output none
  # Key Vault keeps the previous value as an earlier version, so a bad change
  # is recoverable: az keyvault secret list-versions --vault-name ... --name ...
  say "Stored as a new version. The running containers still hold the old value."
  say "Apply it with:  $0"
}

cmd_secrets() {
  say "Secrets in $KEY_VAULT_NAME"
  az keyvault secret list --vault-name "$KEY_VAULT_NAME" \
    --query "[].{name:name, updated:attributes.updated}" -o table
}

cmd_stop() {
  say "Deleting the container group (the Atlas database and its data stay)"
  az container delete --resource-group "$RESOURCE_GROUP" --name "$GROUP_NAME" --yes --output none
  say "Stopped. Compute billing ends here; redeploy with: $0"
}

cmd_destroy() {
  say "This deletes $RESOURCE_GROUP and everything in it."
  say "The Atlas cluster is not touched — delete that in the Atlas UI if you want it gone."
  [[ -t 0 ]] || fail "no terminal attached — run this from a terminal window so the confirmation actually confirms something"
  read -r -p "Type the resource group name to confirm: " confirm || fail "no input received"
  [[ "$confirm" == "$RESOURCE_GROUP" ]] || fail "stopped"
  az group delete --name "$RESOURCE_GROUP" --yes --no-wait
  rm -f "$ENV_FILE" "${STALE_FILES[@]}"
  say "Deletion started (runs in the background)"
}

case "${1:-deploy}" in
  deploy)  cmd_deploy ;;
  status)  cmd_status ;;
  logs)    shift; cmd_logs "${1:-}" ;;
  secret)  shift; cmd_secret "${1:-}" ;;
  secrets) cmd_secrets ;;
  rotate)  cmd_secret "$SECRET_NAME" ;;
  stop)    cmd_stop ;;
  destroy) cmd_destroy ;;
  *)       fail "unknown command: $1 (deploy | status | logs [container] | secret <name> | secrets | rotate | stop | destroy)" ;;
esac
