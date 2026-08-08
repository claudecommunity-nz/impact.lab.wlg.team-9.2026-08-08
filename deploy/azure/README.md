# Deploying to Azure

> **Automatic deployment is currently switched off.**
> The GitHub Actions workflow is disabled and its Azure credentials have been
> deleted from the repository. Nothing deploys on push. See
> [Re-enabling automatic deployment](#re-enabling-automatic-deployment) below.
>
> Deploying by hand from a checkout still works: `./deploy/azure/deploy.sh`.

Compute runs in Azure Container Instances. The database is a free MongoDB Atlas
cluster, outside Azure — real MongoDB, which is what this pipeline was built and
tested against.

When it is switched on, pushes to `main` deploy themselves via
[`.github/workflows/deploy-azure.yml`](../../.github/workflows/deploy-azure.yml).
Before that works, one person runs the bootstrap once.

## Re-enabling automatic deployment

What was removed, and nothing else: the workflow was disabled, and the three
repository **secrets** (`AZURE_CLIENT_ID`, `AZURE_TENANT_ID`,
`AZURE_SUBSCRIPTION_ID`) were deleted.

The eight repository **variables** were deliberately kept — they are resource
names rather than credentials, and they are what the workflow needs to find the
existing deployment rather than build a second one:

```
ACI_DNS_LABEL  ACI_GROUP_NAME  AZURE_ACR_NAME  AZURE_KEY_VAULT_NAME
AZURE_LOCATION  AZURE_RESOURCE_GROUP  AZURE_STORAGE_ACCOUNT  MONGO_SECRET_NAME
```

Also untouched: everything in Azure, everything in Key Vault, and the Entra app
registration with its GitHub OIDC federation. So turning it back on is short.

**If the Azure resources still exist** — re-running the bootstrap recreates the
three secrets from what is already there and changes nothing else:

```bash
az login                              # the account that hosts the deployment
./deploy/azure/bootstrap.sh           # adopts what exists; will not duplicate
gh workflow enable deploy-azure.yml
```

The bootstrap reads the database connection string back out of Key Vault, so it
will not ask you for it. It is safe to re-run.

**If the resource group has been deleted**, the same command rebuilds
everything from nothing — registry, Key Vault, storage, app registration,
federated credentials — and prints what it made. You will need the Atlas
connection string again, and you will want to check the Atlas cluster still
exists, since `deploy.sh destroy` never touched it.

**To confirm it is back on:**

```bash
gh workflow run deploy-azure.yml
gh run watch
```

The workflow smoke tests what it deploys, so a green run means the site is
actually up rather than that `az` returned zero.

### Turning it off again

```bash
gh workflow disable deploy-azure.yml
for s in AZURE_CLIENT_ID AZURE_TENANT_ID AZURE_SUBSCRIPTION_ID; do
  gh secret delete "$s"
done
```

That stops deployments. It does **not** stop the running container group or its
billing — `./deploy/azure/deploy.sh stop` does that — and it does not remove
the Entra app registration, which still trusts this repository's `main` branch.
To revoke that too:

```bash
az ad app delete --id "$(az ad app list \
  --display-name team9-signals-github-deploy --query '[0].appId' -o tsv)"
```

Do that only if you are finished with the deployment. Re-running the bootstrap
recreates it.

## What you have to do by hand

Five things. Everything else is scripted.

### 1. A free MongoDB Atlas cluster

Sign up at [mongodb.com/atlas](https://www.mongodb.com/atlas), then:

- **Create a cluster** — choose **M0** (the free forever tier). Pick the
  nearest region the free tier offers; at this volume latency is irrelevant, so
  don't spend time on it.
- **Create a database user** — Database Access → Add New Database User.
  Use **Autogenerate Secure Password** and copy it now; you can't read it back.
  Role: *Read and write to any database*.
- **Allow network access** — Network Access → Add IP Address → **Allow access
  from anywhere** (`0.0.0.0/0`).
- **Copy the connection string** — Database → Connect → Drivers → Python, then
  replace `<password>` with the real password.

About that `0.0.0.0/0`: ACI's outbound IP isn't guaranteed stable, and every
deploy deletes and recreates the container group, so a narrow allowlist will
break. The protection is the password, not the IP range. That's an acceptable
trade for public hazard-signal data on a one-day prototype, and it would not be
acceptable for anything with personal data in it.

### 2. An Azure account with an active subscription

This is what gets billed. If it isn't the account this machine normally uses,
sign in to it specifically — `az` quietly remembers whichever you used last:

```bash
az logout
az login                              # or: az login --use-device-code
az account list --output table
az account set --subscription "<name or id>"
az account show --query "{user:user.name, sub:name}" -o table
```

The bootstrap prints the account it's about to use and makes you type `yes`.

### 3. Permission to create an app registration in Entra ID

The workflow signs in with OIDC federation, which needs one. Many corporate
tenants block non-admins from creating them. Check first:

```bash
az ad app create --display-name delete-me-permission-check --query appId -o tsv
az ad app delete --id <the id it printed>          # clean up if it worked
```

If it's blocked, ask a tenant admin to run the bootstrap, or use a personal
Azure account. There's a password-based fallback in
[Alternatives](#if-you-cannot-create-an-app-registration) below.

### 4. Run the bootstrap

```bash
./deploy/azure/bootstrap.sh
```

Two or three minutes. It creates the resource group, the container registry,
the app registration with GitHub OIDC federation, a Contributor role assignment
scoped to that one resource group, and a Key Vault. It asks you to paste the
Atlas connection string, **tests it before storing**, puts it in the vault, and
writes the non-secret names into the GitHub repo as variables via the `gh` CLI.

Testing the connection there is deliberate: a wrong password and a missing
network-access entry produce the same symptom half an hour later — containers
that start, then sit failing to reach a database.

Safe to re-run; every step checks for what it already made.

### 5. Set a spending budget

Not scripted, because a budget you didn't set yourself is one you won't trust.
Portal → **Cost Management** → **Budgets** → **Add**:

- Scope: the subscription, or just `team9-signals-rg`
- Amount: whatever ceiling you want against your $150
- Alerts at 50%, 80% and 100% of **actual** cost, to your email

Budget alerts notify. They don't stop anything. The stop button is
`./deploy/azure/deploy.sh stop`.

## Then

```bash
gh workflow run deploy-azure.yml     # or just push to main
```

The workflow builds all four images in parallel, replaces the container group,
and **smoke tests it** — it fails the run if the API never becomes healthy, the
UI doesn't answer, or the GeoJSON isn't valid. A green tick means it's actually
up, not that `az` returned zero. URLs land in the run summary.

## Day-to-day

```bash
./deploy/azure/deploy.sh status    # state and URLs
./deploy/azure/deploy.sh logs      # recent logs from api, scrapers, enrichment
./deploy/azure/deploy.sh logs api  # follow one container
./deploy/azure/deploy.sh           # deploy uncommitted local changes
./deploy/azure/deploy.sh rotate    # replace the database connection string
./deploy/azure/deploy.sh secret anthropic-api-key   # the screenshot intake's vision key
./deploy/azure/deploy.sh stop      # delete the group, keep the data — stops compute billing
./deploy/azure/deploy.sh destroy   # delete the Azure resources
```

`destroy` doesn't touch Atlas. Delete that cluster in the Atlas UI if you want
it gone.

## Where the secrets live

| | Stored where | Notes |
|---|---|---|
| Atlas connection string | **Azure Key Vault**, secret `mongo-uri` | The only copy. Never written to disk, never a GitHub secret |
| Anthropic API key (screenshot intake) | **Azure Key Vault**, secret `anthropic-api-key` | Optional. Absent, the screenshot collector is left out of the deploy and everything else runs |
| Screenshot container SAS | Nowhere persistent | Minted per deploy, 30 days, scoped to that one container |
| Registry password | Nowhere persistent | Fetched fresh from `az acr credential show` each deploy |
| Azure deploy credential | **Nowhere** | OIDC mints a short-lived token per workflow run |
| Azure client / tenant / subscription IDs | GitHub repo secrets | Identifiers, not credentials — useless without the OIDC federation |

The connection string was a GitHub repo secret in an earlier version of this.
It moved because **repo secrets are readable by a workflow on any branch**, so
anyone with push access could print one. The OIDC federation is scoped to
`refs/heads/main`, so reading the vault now takes a merge. `bootstrap.sh`
deletes the old `MONGO_URI` repo secret if it finds one.

Rotating it:

```bash
./deploy/azure/deploy.sh rotate    # prompts, tests, stores a new version
./deploy/azure/deploy.sh           # redeploy to pick it up
```

Key Vault keeps the previous value as an earlier version, so a bad rotation is
recoverable with `az keyvault secret list-versions`.

**What Key Vault does not do here.** ACI has no native Key Vault reference for
environment variables — App Service and Container Apps do, ACI doesn't. So the
deploy pipeline reads the secret and injects it as a `secureValue`, which means
it is still an environment variable inside the running container, readable by
anyone who can `az container exec` into the group. The vault is the source of
truth, the rotation point and the audit trail; it is not a way to hide the
value from the process that needs it.

The rendered container-group definition holds both the registry password and
the connection string in plaintext. It is written to a `0600` file in a temp
directory and deleted when the script exits, including on interrupt — it is no
longer left in the repo as `aci.generated.yaml`.

## Sharing a screenshot into the running deployment

The screenshot intake reads an Azure Blob container, not a folder. A container
group's filesystem is rebuilt on every deploy and nothing outside the container
can write to it, so a local inbox in Azure would be an inbox nobody can reach
that loses its contents on the next push to main.

Drop an image into `inbox/` and the collector picks it up within 20 seconds:

```bash
az storage blob upload \
  --account-name "$(grep STORAGE_ACCOUNT deploy/azure/.azure-env | cut -d= -f2)" \
  --container-name screenshots \
  --name "inbox/$(basename shot.png)" \
  --file shot.png \
  --auth-mode login
```

Or in the portal: **Storage account → Containers → screenshots → inbox →
Upload**, which is drag-and-drop and is what to use during a demo.

The collector then moves each image out of `inbox/` as it deals with it, so
what is left there is exactly what hasn't been looked at:

| Folder | What's in it |
|---|---|
| `inbox/` | Waiting to be read |
| `processed/` | Read successfully, stored under the sha1 of its own bytes. The review page loads these back |
| `skipped/` | Not a social media post |
| `failed/` | Could not be read — a refusal, a timeout, or an unreadable image |

**The stored images are not redacted.** The signal's *text* has handles
stripped and the extraction prompt forbids naming anyone, but the picture still
shows whatever was on screen when it was taken, including the poster's name and
profile photo. That is why the container is private, why the image is served
through the API rather than by a public URL, and why the review page blurs it
until a reviewer asks to see it. Treat the container as holding other people's
personal information, because it does.

## HTTPS

The site is served over HTTPS by a Caddy container in the group, which obtains
and renews the certificate itself over ACME. No key material in this repo,
nothing to remember to rotate, and no custom domain needed.

That last part works because **`*.azurecontainer.io` is on the Public Suffix
List**. That makes the group's full hostname its own registered domain as far
as Let's Encrypt is concerned, so it gets its own rate-limit budget instead of
sharing one with every Azure Container Instance in the world. Had it not been
listed, this would have needed a domain you own.

| Port | What | Why it's open |
|---|---|---|
| 443 | UI and API over HTTPS, via Caddy | the address to share |
| 80 | Caddy | ACME HTTP-01 challenge, and redirects to 443 |
| 8080 | nginx directly, plain HTTP | fallback if TLS is unhappy |
| 8000 | API directly, plain HTTP | direct feed access, unchanged |

Ports 8080 and 8000 stay open deliberately. Certificate issuance depends on
Let's Encrypt reaching us and on rate limits nobody here controls, and a demo
that is entirely down because ACME had a bad afternoon is a worse outcome than
one with a plain-HTTP fallback. The deploy smoke test treats TLS separately for
the same reason: a certificate problem is reported as a warning against a
working app, not as a failed deployment.

**Certificates persist across deploys.** Every deploy deletes and recreates the
container group, and Let's Encrypt allows five duplicate certificates per week
— a day of iterating would exhaust that and leave the site on a rate-limit
error. Caddy's `/data` is therefore an Azure Files share. Unlike the database,
this is a fine use of Azure Files: Caddy wants an ordinary filesystem, not the
locking semantics SMB cannot provide.

## What runs where

| | compose | Azure |
|---|---|---|
| Service addressing | service names (`api`, `mongo`) | `127.0.0.1` — one network namespace per group |
| Scrapers | one container per source | one container running all sources |
| Startup order | `depends_on` + healthchecks | none; each service retries |
| Database | MongoDB container + named volume | MongoDB Atlas M0, outside Azure |

Because the database sits outside the container group, redeploying — which
always deletes and recreates the group — keeps the data.

## Cost against $150

| | |
|---|---|
| Container group (2.0 vCPU, 4.0 GB) | ~US$0.06/hr, ~$1.40/day, billed per second while running |
| Storage account (1 GiB file share) | pennies per month |
| MongoDB Atlas M0 | $0, and not on your Azure bill at all |
| Container registry (Basic) | ~US$0.17/day |
| Key Vault (standard) | ~US$0.03 per 10,000 operations — a few reads a day rounds to nothing |
| Egress, logs | negligible |

So roughly **$1.40/day while it's running**, and the compute part stops the
moment you run `deploy.sh stop`. A weekend costs a few dollars. The way this
budget actually disappears is leaving it up for a month and forgetting — hence
step 5.

M0 gives 512 MB, which is far more than this needs: the whole corpus is a few
hundred kilobytes at hackathon volumes.

## Four things that cost an hour if you don't know them

**ACI runs amd64 only.** A `docker build` on an Apple Silicon Mac produces
arm64, and an arm64 image in an ACI group fails with an unhelpful message. Both
deploy paths use `az acr build --platform linux/amd64`, which builds in Azure —
so the architecture is right regardless of what you're sitting in front of.

**A container group can't be updated in place.** The image tag changes on every
commit and that isn't a mutable field, so both paths delete and recreate. The
DNS label survives; in-group state does not, which is why the database is
outside it.

**`mongodb+srv://` needs dnspython.** Atlas hands out an SRV connection string
and pymongo can't resolve one without it. It's pinned in both
`requirements.txt` files — if you strip it out, you get a connection error that
looks like a network problem and isn't.

**Only one port set per group.** Everything shares one public IP. The group
exposes 80 for the UI and 8000 for the API, and two containers cannot both
listen on the same port.

**Key Vault RBAC takes a minute to bite.** Creating a vault grants you nothing
on its contents, and a role assignment doesn't reach the data plane
immediately — writing a secret straight after granting yourself access fails
with a 403 that looks like a permissions mistake and isn't. `bootstrap.sh`
retries for a minute. Also: a deleted vault holds its name for 90 days under
soft-delete, which is why the name carries a random suffix.

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

## If this outlives the hackathon

Two things to revisit before anyone treats it as more than a prototype: the
`0.0.0.0/0` network rule, and the fact that the cluster lives in an individual's
personal Atlas account rather than anywhere the Council could inherit. Neither
matters for a one-day build; both matter the moment it's handed over.
