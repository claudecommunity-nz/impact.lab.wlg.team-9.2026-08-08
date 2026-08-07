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
#   ./deploy/azure/deploy.sh stop     delete the container group, keep the data
#   ./deploy/azure/deploy.sh destroy  delete everything including the database

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
ENV_FILE="$REPO_ROOT/deploy/azure/.azure-env"
GENERATED="$REPO_ROOT/deploy/azure/aci.generated.yaml"

say()  { printf '\n\033[1m▸ %s\033[0m\n' "$*"; }
fail() { printf '\n\033[31m✗ %s\033[0m\n' "$*" >&2; exit 1; }

az account show >/dev/null 2>&1 || fail "not logged in — run: az login"
[[ -f "$ENV_FILE" ]] || fail "no $ENV_FILE — run ./deploy/azure/bootstrap.sh first"
# shellcheck disable=SC1090
source "$ENV_FILE"

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
  MONGO_URI="$(az cosmosdb keys list \
    --name "$COSMOS_ACCOUNT" --resource-group "$RESOURCE_GROUP" \
    --type connection-strings \
    --query 'connectionStrings[0].connectionString' -o tsv)"
  export ACR_SERVER ACR_USERNAME ACR_PASSWORD MONGO_URI

  "$REPO_ROOT/deploy/azure/render-aci.sh" > "$GENERATED"

  # The rendered file holds the registry password and the database connection
  # string. It is gitignored; say so out loud anyway, because this is exactly
  # the kind of file that gets committed at 3pm.
  say "Wrote $GENERATED — contains live credentials. Gitignored; keep it that way."

  # ACI cannot update a running group in place; replace it. The data is in
  # Cosmos, outside the group, so this costs nothing but a minute of downtime.
  if az container show --resource-group "$RESOURCE_GROUP" --name "$GROUP_NAME" >/dev/null 2>&1; then
    say "Removing the previous container group"
    az container delete --resource-group "$RESOURCE_GROUP" --name "$GROUP_NAME" --yes --output none
  fi

  say "Creating container group $GROUP_NAME (a few minutes)"
  az container create --resource-group "$RESOURCE_GROUP" --file "$GENERATED" --output none

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

cmd_stop() {
  say "Deleting the container group (Cosmos DB and its data stay)"
  az container delete --resource-group "$RESOURCE_GROUP" --name "$GROUP_NAME" --yes --output none
  say "Stopped. Compute billing ends here; redeploy with: $0"
}

cmd_destroy() {
  say "This deletes $RESOURCE_GROUP and everything in it, including the database."
  read -r -p "Type the resource group name to confirm: " confirm
  [[ "$confirm" == "$RESOURCE_GROUP" ]] || fail "stopped"
  az group delete --name "$RESOURCE_GROUP" --yes --no-wait
  rm -f "$ENV_FILE" "$GENERATED"
  say "Deletion started (runs in the background)"
}

case "${1:-deploy}" in
  deploy)  cmd_deploy ;;
  status)  cmd_status ;;
  logs)    shift; cmd_logs "${1:-}" ;;
  stop)    cmd_stop ;;
  destroy) cmd_destroy ;;
  *)       fail "unknown command: $1 (deploy | status | logs [container] | stop | destroy)" ;;
esac
