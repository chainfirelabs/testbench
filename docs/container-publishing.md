# Container publishing

GitLab reads `.gitlab-ci.yml`; GitHub Actions reads
`.github/workflows/publish-images.yml`. Both call
`scripts/ci/publish-images.sh`, which builds directly from the six Dockerfiles
without local `.env` files or Docker Compose.

## Distroless runtime images

All six application images use `gcr.io/distroless/python3-debian13:nonroot`.
Python dependencies are installed in a separate `python:3.13-slim-trixie` build
stage, matching the runtime's Python ABI and Debian release as recommended in
the [distroless Python documentation](https://github.com/GoogleContainerTools/distroless/blob/main/python3/README.md).
Build stages retain their package managers; final images have no shell or package
manager and run as UID/GID 65532. Application files are root-owned and read-only
to that user. Use `/usr/bin/python3 -m <module>` for commands such as Alembic and
Uvicorn; installed console scripts are not runtime entrypoints.

The backend's Python entrypoint applies migrations and starts Uvicorn. Helm
continues to run migrations in its dedicated schema Job and starts Uvicorn
directly in the backend Deployment. Network-scan and reboot retain module
entrypoints so Kubernetes worker arguments select the correct worker mode.

The frontend copies nginx and its linked libraries from Debian 13 into the
distroless runtime. A small Python bootstrap renders the existing nginx template
and replaces itself with nginx, preserving SPA routing, API proxying and optional
HSTS without a shell or `envsubst`. nginx listens on **8080** internally and
writes its PID, rendered config and temporary files under `/tmp`. Custom Docker
port mappings must target 8080; the Helm Service's external port is unchanged.
Custom nginx templates must also listen on 8080. The frontend needs no Linux
capabilities. If enabling a read-only root filesystem, mount a writable `/tmp`.

The externally supplied `oh-my-pi` browser/research image and infrastructure
images such as PostgreSQL are not built by these Dockerfiles and are unchanged.

## Build triggers and tags

Default-branch pushes publish the chart's `appVersion` (currently `1.7.3`),
`latest`, and `sha-<full-commit-SHA>` tags. Rebuilding the same product version
updates its version tag; use a digest or SHA tag to pin an exact build.
Pushing a version tag such as `v1.7.3` publishes `1.7.3` and the SHA tag.
Prerelease tags such as `v1.6.8-rc.1` are supported. Version-tag builds do not
change `latest`. Builds use the runner's native architecture (GitHub's configured
runner is Linux amd64). Feature branches and pull/merge requests do not publish.
Manual runs are available on the default branch or version tags.

Each run builds backend, frontend, mcp, network-scan, device-info, and reboot
before pushing any images. A build/push failure fails the job; registry pushes
across multiple images are not atomic. The external `oh-my-pi` research image
has no Dockerfile in this repository and must be published or mirrored separately.

## GitHub

Enable Actions in the repository. The workflow publishes to
`ghcr.io/<owner>/<repository>/<component>` using the built-in `GITHUB_TOKEN`
with `packages: write`. No extra credentials are needed for this default.
If packages already exist, grant this repository Actions access to those packages.
See [GitHub's publishing documentation](https://docs.github.com/en/actions/tutorials/publish-packages/publish-docker-images).

## GitLab

Enable the project's container registry. The pipeline publishes to
`$CI_REGISTRY_IMAGE/<component>` using GitLab's built-in registry credentials.
The job needs a Docker executor runner configured for privileged Docker-in-Docker
with TLS and a shared `/certs/client` volume, for example in runner `config.toml`:

```toml
[runners.docker]
  privileged = true
  volumes = ["/certs/client", "/cache"]
```

See [GitLab's Docker runner documentation](https://docs.gitlab.com/ci/docker/using_docker_build/)
and [registry authentication documentation](https://docs.gitlab.com/user/packages/container_registry/authenticate_with_container_registry/).

## An internal registry

Public builds use upstream base images by default. Internal GitLab runners can
opt into registry mirrors using private CI/CD variables, without committing
internal hostnames or changing Dockerfiles. GitHub needs no mirror settings.

In GitLab Settings → CI/CD → Variables, configure:

| Variable | Type | Value |
|---|---|---|
| `TB_BUILDKIT_CONFIG` | File | BuildKit TOML containing your internal mirror endpoints |
| `TB_BUILDKIT_IMAGE` | Variable | Reachable BuildKit bootstrap image, including the mirror project path |
| `TB_MIRROR_REGISTRY` | Variable, optional | Mirror hostname for authentication |
| `TB_MIRROR_USER` | Variable, optional | Read-only mirror account |
| `TB_MIRROR_PASSWORD` | Masked variable, optional | Mirror password/token |

Use `scripts/ci/buildkitd.toml.example` as the structure for the File variable,
replacing the example hostnames inside GitLab's settings. GitLab writes the
variable to a temporary file and gives the script its path. Do not put credentials
in the TOML. These settings are independent of destination-registry credentials.
If variables are protected, protect the publishing branch and release tags too.

When a config is supplied, the scripts create a dedicated BuildKit builder,
build with `--load`, and remove the builder afterward. Without a config (or with
`TB_BUILDKIT_CONFIG=direct`), they use the default Docker builder. Local builds
can opt in using the same environment variables and an untracked config outside
the repository. CI uses a temporary Docker credential directory, so private
mirrors need the explicit mirror credential variables even if the host is logged in.

For Docker-in-Docker with TLS, setup first captures the Docker environment in a
temporary named context and passes that context explicitly to Buildx. Cleanup
removes the builder before the context; the active Docker context is unchanged.

The runner must reach and trust the mirror and have the Docker Buildx plugin.
GitLab's job and DinD service images are pulled before this script runs; the
runner's own registry configuration must handle those pulls. k3s registry
settings are not inherited by the builder inside DinD. Mirrors cover container
images, not pip/npm/APT downloads. BuildKit may fall back to the upstream registry
if a mirror fails. See [Docker's BuildKit configuration](https://docs.docker.com/build/buildkit/configure/).

### An internal Python package index

Internal GitLab builds can also point pip at a private index. In the same
Settings → CI/CD → Variables screen, configure:

| Variable | Type | Value |
|---|---|---|
| `TB_PIP_INDEX_URL` | Variable, optional | Complete simple-index URL, for example `https://packages.example.com/root/pypi/+simple/` |
| `TB_PIP_TRUSTED_HOST` | Variable, optional | Hostname to accept over plain HTTP; only for an HTTP-only index |

Use your own index path, and mask the value if it embeds credentials. The
publisher passes these to the five Python builds as BuildKit secrets, referenced
by variable name so no URL reaches a build's command line. Each value is readable
only while pip runs, including in isolated build-dependency environments, and
appears in no image layer or image configuration. The frontend builds with npm
rather than pip and receives neither secret.

GitHub ignores both variables and builds against the default PyPI index, as do
GitLab pipelines that leave them unset. `TB_PIP_TRUSTED_HOST` applies only
alongside `TB_PIP_INDEX_URL`; on its own it is ignored, and without it pip's
usual HTTPS verification is unchanged. HTTP package downloads are unencrypted,
so restrict them to a trusted internal network. No internal package hostname
belongs in a Dockerfile or in the workflow YAML.

pip reports its configured source in the build log as `Looking in indexes:
https://...`, with any password redacted but the hostname and username still
printed; mask the variable if that is sensitive. Changing a secret does not
invalidate Docker's build cache, so clear the builder cache when switching
indexes must force a fresh dependency download.

### A proxy for npm and Debian packages

The frontend build is the only one that fetches from the public internet: npm
from its registry, and apt from the Debian archives. The other five images
install Python packages, which `TB_PIP_INDEX_URL` already points at an internal
index, and every image's base layers come from the registry mirrors above. On a
runner without direct internet access, the frontend build is what needs a proxy.

| Variable | Type | Value |
|---|---|---|
| `TB_HTTP_PROXY` | Variable, optional | Proxy URL for npm and Debian downloads, for example `http://squid.example.com:3128` |

The publisher passes this only to GitLab frontend builds, as a BuildKit secret.
The npm installation receives temporary `npm_config_proxy` and
`npm_config_https_proxy` settings. The APT installation receives temporary
`http_proxy` and `https_proxy` settings. No proxy configuration files are written,
and the URL is not passed in build arguments or stored in image configuration.
Later build commands and the runtime do not inherit these secret variables.

Unset or empty means no proxy override is supplied; npm and APT use their normal
connection settings. GitHub ignores this variable. Changing the secret does not
invalidate existing dependency layers, so a changed proxy does not force an
otherwise unnecessary reinstall. To test connectivity through a new proxy,
use a fresh builder or rebuild without cache. Mask the CI variable if its value
contains credentials; tool error messages can still mention the proxy endpoint.

Allow `registry.npmjs.org`, `deb.debian.org` and `security.debian.org` on the
proxy. npm needs `CONNECT` for HTTPS; the Debian downloads are plain HTTP, and
their integrity comes from signed release files rather than the transport. An
unreachable proxy surfaces poorly in npm, as a retry loop ending in
`npm error Exit handler never called!` rather than a connection error, so check
proxy reachability first when that appears.

Configure these on each platform; nothing secret belongs in the YAML files:

| Setting | Example | GitHub storage | GitLab storage |
|---|---|---|---|
| `TB_REGISTRY` | `registry.internal.example:5000` | Actions variable | CI/CD variable |
| `TB_IMAGE_PREFIX` | `registry.internal.example:5000/chainfirelabs/testbench` | Actions variable | CI/CD variable |
| `TB_REGISTRY_USER` | registry service account | Actions secret | CI/CD variable |
| `TB_REGISTRY_PASSWORD` | registry password/token | Actions secret | Masked CI/CD variable |

`TB_REGISTRY` is a host with an optional port, without `https://` or a path.
`TB_IMAGE_PREFIX` includes that host plus the namespace/project path; the script
appends `/<component>`. If omitted, the prefix uses the selected registry plus
the platform's project path. Custom registries require both credential settings.
If GitLab variables are protected, protect the publishing branch and release tags.
Runners must be able to reach the registry and trust its TLS certificate; use a
self-hosted GitHub runner if the registry is only reachable on your private network.
Each platform publishes to one registry per run. Setting the same custom prefix
on both makes them publish to the same destination.

## Use the images in Helm

The chart defaults to GHCR: `global.imageRegistry: ghcr.io` with repositories
such as `chainfirelabs/testbench/backend` and `chainfirelabs/testbench/frontend`.
These match GitHub workflow publications from the `chainfirelabs/testbench`
repository. For a different GitHub owner/project, update the component repository
paths accordingly. The default tag is `1.7.3`; publish Git tag `v1.7.3` to produce
that image tag, or override the chart tags to another published version.

GitLab defaults to its project registry. Override `global.imageRegistry` and,
if its project path differs, the component repository paths to use those images.
The external research image must be published/mirrored separately and configured
under each plugin's `researchImage`; the workflow does not build it. Set
`researchImage.registry` to its registry to override the global default, or
use `registry: ""` with a fully qualified repository.

To publish to Docker Hub from CI, set `TB_REGISTRY=docker.io`,
`TB_IMAGE_PREFIX=docker.io/chainfirelabs`, and supply destination credentials.
Then set the chart registry to `docker.io` and repositories to `chainfirelabs/backend`,
`chainfirelabs/frontend`, etc. Docker Hub uses one namespace and one repository
name; local build examples likewise use `TB_REGISTRY=docker.io/chainfirelabs`.

Set component tags to the published version, `latest`, or SHA tag; version tags
strip the leading `v`. Configure `global.imagePullSecrets` for private images,
and set both plugins' `researchImage.repository` to your mirrored research image.
Publishing containers does not upgrade a Kubernetes release.

Local publisher regression checks (Docker is mocked; no images are pushed):

```sh
python3 -m unittest discover -s scripts/ci/tests
```

## Frontend version label

The bottom-left app label shows the frontend's build version. Image publishing
passes the release version through Docker's `VERSION` build argument, which the
frontend build exposes as `VITE_APP_VERSION`. The same value labels the image;
changing a Helm image tag does not rewrite a previously built frontend.

For a direct image build, pass `--build-arg VERSION=1.7.3`. For a local frontend
build or dev server, set `VITE_APP_VERSION=1.7.3` when running `npm run build` or
`npm run dev` in `src/frontend`. Without a version, the label is `TestBench dev`.
