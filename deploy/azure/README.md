# Deploying to Azure Container Instances

```bash
az login
./deploy/azure/deploy.sh
```

That creates a resource group, a container registry, builds all four images
**in Azure**, and starts a five-container group. It prints the public URLs when
it finishes. Ten minutes or so the first time, a couple of minutes after that.

```bash
./deploy/azure/deploy.sh status         # state + URLs
./deploy/azure/deploy.sh logs           # recent logs from api, scrapers, enrichment
./deploy/azure/deploy.sh logs scrapers  # follow one container
./deploy/azure/deploy.sh destroy        # delete everything
```

Generated names are cached in `.azure-env` (gitignored), so re-running updates
the same deployment rather than creating a second one. Delete that file to
start fresh.

## What's different from `docker compose up`

| | compose | ACI |
|---|---|---|
| Service addressing | service names (`api`, `mongo`) | `127.0.0.1` — one network namespace per group |
| Scrapers | one container per source | one container running all sources |
| Startup order | `depends_on` + healthchecks | none; each service retries |
| Mongo data | named volume, survives restarts | ephemeral, lost on restart |

## Four things that will cost you an hour if you don't know them

**ACI runs amd64 only.** A `docker build` on an Apple Silicon Mac produces
arm64, and an arm64 image in an ACI group fails with an unhelpful message. The
script uses `az acr build --platform linux/amd64`, which builds in Azure — so
the architecture is right regardless of what you're sitting in front of.

**MongoDB cannot run on an Azure Files volume.** WiredTiger needs file locking
that SMB/CIFS doesn't provide. Mounting one looks like it works, then corrupts.
The group therefore uses ephemeral storage — see below if you need persistence.

**A container group can't be updated in place.** Changing the definition means
delete and recreate, which the script does for you. Expect the public IP to
stay (the DNS label does) but the data not to.

**Only one port set per group.** Everything shares one public IP. The group
exposes 80 for the UI and 8000 for the API. Two containers cannot both listen
on the same port.

## If you need the data to survive a restart

Swap Mongo for Cosmos DB's MongoDB API — it speaks the same wire protocol, so
nothing in the code changes:

```bash
az cosmosdb create \
  --name team9-signals-db --resource-group team9-signals-rg \
  --kind MongoDB --server-version 4.2

az cosmosdb keys list \
  --name team9-signals-db --resource-group team9-signals-rg \
  --type connection-strings \
  --query 'connectionStrings[0].connectionString' -o tsv
```

Then in `aci.template.yaml`: delete the `mongo` container and set `MONGO_URI`
on both `api` and `enrichment` to that connection string. Use a secure
environment variable (`secureValue:` instead of `value:`) so the connection
string doesn't show up in `az container show`.

Worth knowing: Cosmos's Mongo API doesn't support every operator. Nothing this
pipeline uses is exotic, but `$geoNear` behaves differently if you extend it.

## Cost

Roughly 2.75 vCPU and 5 GB, billed per second while the group runs. On the
order of a few dollars a day. `./deploy/azure/deploy.sh destroy` when you're
finished with it — a forgotten container group is the classic hackathon
invoice.
