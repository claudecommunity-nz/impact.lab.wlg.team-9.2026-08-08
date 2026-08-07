# Deploying to Azure

Pushes to `main` deploy themselves via
[`.github/workflows/deploy-azure.yml`](../../.github/workflows/deploy-azure.yml).
Before that works, one person runs the bootstrap once.

## What you have to do by hand

Four things. Everything else is scripted.

**1. An Azure account with an active subscription.** This is the account that
gets billed. If it isn't the one this machine is normally signed in to, sign in
to it specifically:

```bash
az logout
az login                              # or: az login --use-device-code
az account list --output table        # check which subscriptions you can see
az account set --subscription "<name or id>"
az account show --query "{user:user.name, sub:name}" -o table
```

The bootstrap script prints the account it's about to use and makes you type
`yes`, precisely because `az` quietly remembers whichever account you used last.

**2. Permission to create an app registration in Entra ID.** The workflow signs
in with OIDC federation, which needs an app registration. Many corporate
tenants block non-admins from creating them. Check first:

```bash
az ad app create --display-name delete-me-permission-check --query appId -o tsv
# if that works, clean it up:
az ad app delete --id <the id it printed>
```

If it's blocked, ask a tenant admin to run the bootstrap, or use a personal
Azure account for the hackathon. There is a password-based fallback in
[Alternatives](#if-you-cannot-create-an-app-registration) below.

**3. Run the bootstrap.**

```bash
./deploy/azure/bootstrap.sh
```

Ten to fifteen minutes, most of it waiting for Cosmos DB. It creates the
resource group, container registry, Cosmos DB account and database, the app
registration with GitHub OIDC federation, and a Contributor role assignment
scoped to the one resource group. Then it writes the resulting IDs into the
GitHub repo as secrets and variables via the `gh` CLI — or prints them for you
to paste if `gh` isn't authenticated.

Safe to re-run; every step checks for what it already made.

**4. Set a spending budget.** Not scripted, because a budget you didn't set
yourself is a budget you won't trust. Portal → **Cost Management** → **Budgets**
→ **Add**:

- Scope: the subscription (or just the `team9-signals-rg` resource group)
- Amount: whatever ceiling you want against your $150
- Alerts at 50%, 80% and 100% of **actual** cost, to your email

Budget alerts notify. They do not stop anything from running. The stop button
is `./deploy/azure/deploy.sh stop`.

## Then

```bash
gh workflow run deploy-azure.yml     # or just push to main
```

The workflow builds all four images in parallel, replaces the container group,
and **smoke tests it** — it fails the run if the API never becomes healthy, the
UI doesn't answer, or the GeoJSON isn't valid. A green tick means it's actually
up, not just that `az` returned zero. The URLs land in the run summary.

## Day-to-day

```bash
./deploy/azure/deploy.sh status    # state and URLs
./deploy/azure/deploy.sh logs      # recent logs from api, scrapers, enrichment
./deploy/azure/deploy.sh logs api  # follow one container
./deploy/azure/deploy.sh           # deploy uncommitted local changes
./deploy/azure/deploy.sh stop      # delete the group, keep the data — stops compute billing
./deploy/azure/deploy.sh destroy   # delete everything, database included
```

## What runs where

| | compose | Azure |
|---|---|---|
| Service addressing | service names (`api`, `mongo`) | `127.0.0.1` — one network namespace per group |
| Scrapers | one container per source | one container running all sources |
| Startup order | `depends_on` + healthchecks | none; each service retries |
| Database | MongoDB container + named volume | Cosmos DB, outside the group |

Because Cosmos sits outside the container group, redeploying — which always
deletes and recreates the group — keeps the data.

## Cost against $150

| | |
|---|---|
| Container group (1.75 vCPU, 3.5 GB) | ~US$0.05/hr, ~$1.20/day, billed per second while running |
| Cosmos DB free tier | $0 — 1000 RU/s and 25 GB, one account per subscription |
| Cosmos DB serverless (fallback) | cents/day at this volume |
| Container registry (Basic) | ~US$0.17/day |
| Egress, logs | negligible |

So roughly **$1.50/day left running**, and the compute part stops the moment
you run `deploy.sh stop`. A weekend costs a few dollars. The way this budget
actually gets spent is leaving it running for a month and forgetting — hence
step 4.

The bootstrap uses the Cosmos **free tier** if the subscription hasn't already
used it (only one account per subscription may have it) and falls back to
**serverless** if it has. Either is fine here; it tells you which it picked.

## Four things that cost an hour if you don't know them

**ACI runs amd64 only.** A `docker build` on an Apple Silicon Mac produces
arm64, and an arm64 image in an ACI group fails with an unhelpful message. Both
deploy paths use `az acr build --platform linux/amd64`, which builds in Azure —
so the architecture is right regardless of what you're sitting in front of.

**A container group can't be updated in place.** The image tag changes on every
commit and that isn't a mutable field, so both paths delete and recreate. The
DNS label survives; in-group state does not, which is why the database is
outside it.

**Cosmos DB is not MongoDB.** It speaks the wire protocol, and everything this
pipeline does is supported, but it refuses a unique index on a collection that
already holds data. `ensure_indexes` therefore attempts each index
independently and logs anything the server refuses rather than failing to
start — check the API log after the first deploy to confirm both unique indexes
were created. Without them, re-scraping stops de-duplicating.

**Only one port set per group.** Everything shares one public IP. The group
exposes 80 for the UI and 8000 for the API, and two containers cannot both
listen on the same port.

## If you cannot create an app registration

Fall back to a service principal with a secret. Someone with rights runs:

```bash
az ad sp create-for-rbac \
  --name team9-signals-github-deploy \
  --role Contributor \
  --scopes /subscriptions/<sub-id>/resourceGroups/team9-signals-rg \
  --json-auth
```

Put the whole JSON blob in a repo secret called `AZURE_CREDENTIALS`, then in
both `azure/login@v2` steps replace the three `client-id`/`tenant-id`/
`subscription-id` lines with `creds: ${{ secrets.AZURE_CREDENTIALS }}`.

This stores a password that expires (a year by default) and has to be rotated.
OIDC is better where the tenant allows it.

## If the data needs to outlive the hackathon

It already does — Cosmos persists independently of the container group. But
Cosmos free tier is not a backup. `az cosmosdb mongodb collection show` and
the portal's point-in-time restore are worth a look before anyone treats this
as anything other than a prototype.
