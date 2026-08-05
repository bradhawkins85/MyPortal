# n8n MyPortal Node

Community-node package that adds a **MyPortal** action node for Staff and Tickets.

## Supported actions

For both `Staff` and `Ticket` resources:

- Create
- Get
- Get All
- Update
- Delete

## Authentication

Create MyPortal API credentials in n8n with:

- **Base URL**: your MyPortal URL, for example `https://portal.example.com`
- **API Key**: a MyPortal API key sent as the `x-api-key` header

## Build artifact from GitHub Actions

The repository includes a GitHub Actions workflow at `.github/workflows/n8n-node-myportal.yml`.
It runs when the n8n node package or workflow changes, and can also be started manually from the
GitHub **Actions** tab with **Build n8n MyPortal Node → Run workflow**.

The workflow:

1. Uses Node.js 22, matching current n8n dependency requirements.
2. Runs `npm ci`.
3. Runs `npm run lint` for TypeScript type checking.
4. Runs `npm run build` to compile the node into `dist/`.
5. Runs `npm pack` and uploads the generated `n8n-nodes-myportal-*.tgz` as a workflow artifact.

Download the `n8n-nodes-myportal` artifact from a successful workflow run when you want to install
the built package without publishing it to npm. GitHub downloads workflow artifacts as `.zip` files;
unzip the artifact first and install the `n8n-nodes-myportal-*.tgz` file inside it. Do not rename
the downloaded `.zip` to `.tgz`: that still leaves a ZIP file on disk, and `npm install` will fail
with `TAR_BAD_ARCHIVE` because npm expects a gzip-compressed npm package tarball, not GitHub's
artifact wrapper.

## Install in n8n

### Option 1: Install from an npm registry

After publishing this package to an npm registry, install it from n8n:

1. Open n8n.
2. Go to **Settings → Community Nodes**.
3. Select **Install**.
4. Enter `n8n-nodes-myportal`.
5. Confirm the install and restart n8n if your deployment requires it.

For self-hosted n8n, you can also install from the n8n user directory:

```bash
cd ~/.n8n
npm install n8n-nodes-myportal
```

### Option 2: Install the GitHub Actions artifact

Use this when the package has not been published to npm yet.

1. Run the **Build n8n MyPortal Node** GitHub Actions workflow.
2. Download the `n8n-nodes-myportal` artifact `.zip`.
3. Unzip it and verify that you have the generated `n8n-nodes-myportal-*.tgz` file:

   ```bash
   unzip n8n-nodes-myportal.zip
   file n8n-nodes-myportal-0.1.0.tgz
   tar -tzf n8n-nodes-myportal-0.1.0.tgz >/dev/null
   ```

   The `file` command should report gzip-compressed data. If it reports Zip archive data, you are
   still pointing npm at the GitHub artifact wrapper and need to unzip it first.

   You can also verify that the tarball includes the compiled node files before installing it:

   ```bash
   tar -tzf n8n-nodes-myportal-0.1.0.tgz | grep -E 'package/dist/(nodes/MyPortal/MyPortal.node.js|credentials/MyPortalApi.credentials.js|nodes/MyPortal/myportal.svg)$'
   ```

4. Copy or upload only the extracted `.tgz` file to your n8n host.
5. Install it from the n8n user directory:

```bash
cd ~/.n8n
npm install /path/to/n8n-nodes-myportal-0.1.0.tgz
```

6. Restart n8n, then create a **MyPortal API** credential and add the **MyPortal** node to a workflow.

If `npm install /path/to/n8n-nodes-myportal-0.1.0.tgz` reports that it added a package but the node
does not appear, first make sure you are installing a package built from this version or newer. Older
tarballs declared `n8n-workflow` as a peer dependency, which can make npm install an extra copy of
n8n runtime packages under the community node package. That install path can trigger messages such as
`1 package had install scripts blocked because they are not covered by allowScripts` for
`isolated-vm`, and n8n may then skip or fail to load the community node even though npm reports a
successful install. This package keeps `n8n-workflow` as a development-only dependency so the packed
community node contains only the compiled MyPortal node and credential files.

Then confirm that the command ran in the n8n user directory used by the running process (typically
`/home/node/.n8n` in Docker, not the host's `~/.n8n`) and check that n8n can see the package metadata:

```bash
cd ~/.n8n
node -e "const p=require('./node_modules/n8n-nodes-myportal/package.json'); console.log(p.n8n.nodes, p.n8n.credentials)"
```

If that command fails or points to an old package, remove and reinstall the package in the same
directory, then restart n8n:

```bash
cd ~/.n8n
npm remove n8n-nodes-myportal n8n-workflow @n8n/expression-runtime isolated-vm
npm install /path/to/n8n-nodes-myportal-0.1.0.tgz
```

After reinstalling, this package should not add `n8n-workflow`, `@n8n/expression-runtime`, or
`isolated-vm` under `~/.n8n/node_modules` as dependencies of `n8n-nodes-myportal`. If npm still asks
you to approve the `isolated-vm` install script while installing only this tarball, verify that you
are using a freshly generated tarball and not an older artifact.

### Option 3: Install in Docker

For Docker-based n8n deployments, mount or copy the tarball into the container and install it in the
n8n user directory. One common pattern is:

```bash
# If you downloaded the GitHub Actions artifact, unzip it on your host first.
unzip n8n-nodes-myportal.zip
docker cp n8n-nodes-myportal-0.1.0.tgz <n8n-container>:/tmp/
docker exec -it <n8n-container> sh -lc 'cd ~/.n8n && npm install /tmp/n8n-nodes-myportal-0.1.0.tgz'
docker restart <n8n-container>
```

For immutable production images, bake the tarball install into your custom n8n image instead of
installing it manually in a running container.

## Curl replacements for the n8n node

If the n8n community node is not working, you can perform the same HTTP actions directly with `curl`. The node sends the API key as `x-api-key`, uses JSON request bodies for create/update operations, and trims trailing slashes from the configured base URL.

Set these variables once before running the examples:

```bash
BASE_URL="https://portal.example.com"
API_KEY="replace-with-your-myportal-api-key"
```

All JSON examples use single quotes around the shell argument so the JSON is passed to MyPortal unchanged. Replace IDs, emails, company IDs, requester IDs, and assignee IDs with values from your portal.

### Staff actions

#### Get many staff

Matches the n8n **Staff → Get Many** operation. `companyId`, `email`, and `accountAction` are optional filters.

```bash
curl -sS -G "${BASE_URL%/}/api/staff" \
  -H "x-api-key: ${API_KEY}" \
  -H "Accept: application/json" \
  --data-urlencode "companyId=123" \
  --data-urlencode "email=jane.doe@example.com" \
  --data-urlencode "accountAction=onboard"
```

For the company-scoped polling fields that are available on the same endpoint, add filters such as `updatedAfter`, `cursor`, and `pageSize`:

```bash
curl -sS -G "${BASE_URL%/}/api/staff" \
  -H "x-api-key: ${API_KEY}" \
  -H "Accept: application/json" \
  --data-urlencode "companyId=123" \
  --data-urlencode "updatedAfter=2026-08-01T00:00:00Z" \
  --data-urlencode "pageSize=200"
```

#### Get one staff member

Matches **Staff → Get**.

```bash
STAFF_ID=456

curl -sS "${BASE_URL%/}/api/staff/${STAFF_ID}" \
  -H "x-api-key: ${API_KEY}" \
  -H "Accept: application/json"
```

#### Create staff

Matches **Staff → Create**. The n8n node maps `companyId`, `firstName`, `lastName`, `mobilePhone`, `jobTitle`, `accountAction`, and `customFields` using camelCase JSON keys.

```bash
curl -sS -X POST "${BASE_URL%/}/api/staff" \
  -H "x-api-key: ${API_KEY}" \
  -H "Accept: application/json" \
  -H "Content-Type: application/json" \
  --data '{
    "companyId": 123,
    "firstName": "Jane",
    "lastName": "Doe",
    "email": "jane.doe@example.com",
    "mobilePhone": "+15551234567",
    "enabled": true,
    "department": "Operations",
    "jobTitle": "Operations Manager",
    "accountAction": "onboard",
    "customFields": {
      "Employee Number": "E-10045"
    }
  }'
```

#### Update staff

Matches **Staff → Update**. Send only fields you want to change.

```bash
STAFF_ID=456

curl -sS -X PUT "${BASE_URL%/}/api/staff/${STAFF_ID}" \
  -H "x-api-key: ${API_KEY}" \
  -H "Accept: application/json" \
  -H "Content-Type: application/json" \
  --data '{
    "mobilePhone": "+15557654321",
    "department": "Service Desk",
    "jobTitle": "Senior Technician",
    "enabled": true,
    "customFields": {
      "Employee Number": "E-10045"
    }
  }'
```

#### Delete staff

Matches **Staff → Delete**. MyPortal returns `204 No Content` on success.

```bash
STAFF_ID=456

curl -sS -i -X DELETE "${BASE_URL%/}/api/staff/${STAFF_ID}" \
  -H "x-api-key: ${API_KEY}" \
  -H "Accept: application/json"
```

### Ticket actions

#### Get many tickets

Matches **Ticket → Get Many**. The n8n node calls `/api/tickets/` and passes optional `status`, `company_id`, `assigned_user_id`, `search`, and `limit` query parameters.

```bash
curl -sS -G "${BASE_URL%/}/api/tickets/" \
  -H "x-api-key: ${API_KEY}" \
  -H "Accept: application/json" \
  --data-urlencode "status=open" \
  --data-urlencode "company_id=123" \
  --data-urlencode "assigned_user_id=789" \
  --data-urlencode "search=printer offline" \
  --data-urlencode "limit=50"
```

The API returns a wrapper object for ticket searches. n8n flattens `items`, so use `jq '.items[]'` if you want similar one-record-at-a-time output:

```bash
curl -sS -G "${BASE_URL%/}/api/tickets/" \
  -H "x-api-key: ${API_KEY}" \
  -H "Accept: application/json" \
  --data-urlencode "search=printer offline" \
  --data-urlencode "limit=50" | jq '.items[]'
```

#### Get one ticket

Matches **Ticket → Get**.

```bash
TICKET_ID=1001

curl -sS "${BASE_URL%/}/api/tickets/${TICKET_ID}" \
  -H "x-api-key: ${API_KEY}" \
  -H "Accept: application/json"
```

#### Create ticket

Matches **Ticket → Create**. When using API-key authentication, `requester_id` is required.

```bash
curl -sS -X POST "${BASE_URL%/}/api/tickets/" \
  -H "x-api-key: ${API_KEY}" \
  -H "Accept: application/json" \
  -H "Content-Type: application/json" \
  --data '{
    "subject": "Printer offline",
    "description": "The reception printer is offline and users cannot print.",
    "status": "open",
    "priority": "normal",
    "requester_id": 456,
    "company_id": 123,
    "assigned_user_id": 789
  }'
```

#### Update ticket

Matches **Ticket → Update**. Send only fields you want to change.

```bash
TICKET_ID=1001

curl -sS -X PUT "${BASE_URL%/}/api/tickets/${TICKET_ID}" \
  -H "x-api-key: ${API_KEY}" \
  -H "Accept: application/json" \
  -H "Content-Type: application/json" \
  --data '{
    "status": "in_progress",
    "priority": "high",
    "assigned_user_id": 789,
    "description": "Updated ticket description from external automation."
  }'
```

#### Delete ticket

Matches **Ticket → Delete**. MyPortal returns `204 No Content` on success.

```bash
TICKET_ID=1001

curl -sS -i -X DELETE "${BASE_URL%/}/api/tickets/${TICKET_ID}" \
  -H "x-api-key: ${API_KEY}" \
  -H "Accept: application/json"
```

### Raw JSON body equivalents

The n8n node's **Raw JSON Body** field is merged into the generated request body for create and update operations. In `curl`, include those advanced fields directly in the JSON payload. For example, to create a ticket with fields not exposed as dedicated n8n inputs:

```bash
curl -sS -X POST "${BASE_URL%/}/api/tickets/" \
  -H "x-api-key: ${API_KEY}" \
  -H "Accept: application/json" \
  -H "Content-Type: application/json" \
  --data '{
    "subject": "Quarterly access review",
    "description": "Please complete the quarterly review.",
    "requester_id": 456,
    "company_id": 123,
    "category": "security",
    "module_slug": "compliance",
    "external_reference": "n8n-fallback-2026-08"
  }'
```

## Development

Use Node.js 22 or 24 when installing and building this package locally. Current n8n development
dependencies include native modules that do not build correctly on older Node.js versions.

```bash
npm install
npm run build
```

If `npm install` fails with an error like
`404 Not Found - GET https://registry.npmjs.org/@npmcli%2fdocs`, the missing package is not a
MyPortal dependency. It is usually caused by a broken or stale npm CLI/cache while npm is resolving
dependencies. Switch to a supported Node.js release, refresh npm's cache, and reinstall from a clean
dependency tree:

```bash
node --version
npm --version
npm cache verify
rm -rf node_modules package-lock.json
npm install
```

If the same `@npmcli/docs` 404 continues after the clean install, reinstall Node.js 22 or 24 with a
fresh bundled npm and run `npm install` again before changing this package's dependencies.
