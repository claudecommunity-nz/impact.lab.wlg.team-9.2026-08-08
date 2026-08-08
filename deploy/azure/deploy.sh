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
#   ./deploy/azure/deploy.sh rotate   replace the database connection string
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
SECRET_NAME="${SECRET_NAME:-mongo-uri}"

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
  export ACR_SERVER ACR_USERNAME ACR_PASSWORD MONGO_URI

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

  UI        http://$fqdn/
  API docs  http://$fqdn:8000/docs
  Stats     http://$fqdn:8000/stats
  Signals   http://$fqdn:8000/signals.geojson
  Groups    http://$fqdn:8000/clusters.geojson

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

cmd_rotate() {
  say "Replacing '$SECRET_NAME' in Key Vault $KEY_VAULT_NAME"
  echo "  Paste the new Atlas connection string (input hidden)."
  read -r -s -p "  > " new_uri
  echo

  [[ -n "$new_uri" ]] || fail "nothing entered"
  [[ "$new_uri" == mongodb+srv://* || "$new_uri" == mongodb://* ]] \
    || fail "that does not look like a MongoDB connection string"
  [[ "$new_uri" != *"<password>"* ]] || fail "the <password> placeholder is still in there"

  if command -v docker >/dev/null 2>&1 && docker info >/dev/null 2>&1; then
    say "Testing it before storing"
    docker run --rm mongo:7 mongosh "$new_uri" --quiet \
      --eval 'db.adminCommand({ping:1}).ok' 2>/dev/null | grep -q 1 \
      || fail "could not connect with that string — nothing was changed"
    say "Connected"
  fi

  az keyvault secret set --vault-name "$KEY_VAULT_NAME" --name "$SECRET_NAME" \
    --value "$new_uri" --output none
  # Key Vault keeps the old value as a previous version, so a bad rotation is
  # recoverable: az keyvault secret list-versions --vault-name ... --name ...
  say "Stored as a new version. The running containers still hold the old value."
  say "Apply it with:  $0"
}

cmd_stop() {
  say "Deleting the container group (the Atlas database and its data stay)"
  az container delete --resource-group "$RESOURCE_GROUP" --name "$GROUP_NAME" --yes --output none
  say "Stopped. Compute billing ends here; redeploy with: $0"
}

cmd_destroy() {
  say "This deletes $RESOURCE_GROUP and everything in it."
  say "The Atlas cluster is not touched — delete that in the Atlas UI if you want it gone."
  read -r -p "Type the resource group name to confirm: " confirm
  [[ "$confirm" == "$RESOURCE_GROUP" ]] || fail "stopped"
  az group delete --name "$RESOURCE_GROUP" --yes --no-wait
  rm -f "$ENV_FILE" "${STALE_FILES[@]}"
  say "Deletion started (runs in the background)"
}

case "${1:-deploy}" in
  deploy)  cmd_deploy ;;
  status)  cmd_status ;;
  logs)    shift; cmd_logs "${1:-}" ;;
  rotate)  cmd_rotate ;;
  stop)    cmd_stop ;;
  destroy) cmd_destroy ;;
  *)       fail "unknown command: $1 (deploy | status | logs [container] | rotate | stop | destroy)" ;;
esac
