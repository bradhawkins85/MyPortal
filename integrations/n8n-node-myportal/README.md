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
unzip the artifact first and install the `n8n-nodes-myportal-*.tgz` file inside it. Installing the
artifact `.zip` directly with npm causes `TAR_BAD_ARCHIVE` / checksum errors because npm expects a
gzip-compressed npm package tarball, not GitHub's artifact wrapper.

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
3. Unzip it and verify that you have the generated `n8n-nodes-myportal-*.tgz` file.
4. Copy or upload only the `.tgz` file to your n8n host.
5. Install it from the n8n user directory:

```bash
cd ~/.n8n
npm install /path/to/n8n-nodes-myportal-0.1.0.tgz
```

6. Restart n8n, then create a **MyPortal API** credential and add the **MyPortal** node to a workflow.

### Option 3: Install in Docker

For Docker-based n8n deployments, mount or copy the tarball into the container and install it in the
n8n user directory. One common pattern is:

```bash
docker cp n8n-nodes-myportal-0.1.0.tgz <n8n-container>:/tmp/
docker exec -it <n8n-container> sh -lc 'cd ~/.n8n && npm install /tmp/n8n-nodes-myportal-0.1.0.tgz'
docker restart <n8n-container>
```

For immutable production images, bake the tarball install into your custom n8n image instead of
installing it manually in a running container.

## Development

```bash
npm install
npm run build
```
