#!/bin/sh
# Shared GitHub Actions / GitLab CI publisher; no .env or Compose dependency.
set -eu

if [ "${GITHUB_ACTIONS:-}" = true ]; then
  default_registry=ghcr.io
  project=$(printf '%s' "$GITHUB_REPOSITORY" | tr '[:upper:]' '[:lower:]')
  commit=$GITHUB_SHA
  source_url="$GITHUB_SERVER_URL/$GITHUB_REPOSITORY"
  release_tag=""
  [ "$GITHUB_REF_TYPE" != tag ] || release_tag=$GITHUB_REF_NAME
  default_user=$GITHUB_ACTOR
  default_password=${GITHUB_TOKEN:-}
elif [ "${GITLAB_CI:-}" = true ]; then
  default_registry=$CI_REGISTRY
  project=${CI_REGISTRY_IMAGE#"$CI_REGISTRY/"}
  commit=$CI_COMMIT_SHA
  source_url=$CI_PROJECT_URL
  release_tag=${CI_COMMIT_TAG:-}
  default_user=$CI_REGISTRY_USER
  default_password=$CI_REGISTRY_PASSWORD
else
  echo 'Run this publisher through GitHub Actions or GitLab CI.' >&2
  exit 1
fi

registry=${TB_REGISTRY:-$default_registry}
registry=${registry%/}
prefix=${TB_IMAGE_PREFIX:-$registry/$project}
prefix=${prefix%/}
case "$registry" in
  *://*|*/*|'') echo 'TB_REGISTRY must be a registry host with an optional port.' >&2; exit 1 ;;
esac
case "$prefix" in
  "$registry/"?*) ;;
  *) echo 'TB_IMAGE_PREFIX must start with TB_REGISTRY followed by a repository path.' >&2; exit 1 ;;
esac
if [ "$registry" != "$default_registry" ]; then
  : "${TB_REGISTRY_USER:?Set TB_REGISTRY_USER for a custom registry}"
  : "${TB_REGISTRY_PASSWORD:?Set TB_REGISTRY_PASSWORD for a custom registry}"
fi
username=${TB_REGISTRY_USER:-$default_user}
password=${TB_REGISTRY_PASSWORD:-$default_password}
: "${password:?Registry password is required}"

repo_root=$(CDPATH= cd -- "$(dirname -- "$0")/../.." && pwd)
# Branch builds use the product version too, so chart tags and the frontend
# label match the images published from main. Explicit release tags take priority.
version=$(sed -n 's/^appVersion: *"\([^"]*\)" *$/\1/p' "$repo_root/helm/testbench/Chart.yaml")
if [ -n "$release_tag" ]; then
  version=${release_tag#v}
fi
if ! printf '%s\n' "$version" | LC_ALL=C grep -Eq '^[0-9]+\.[0-9]+\.[0-9]+(-[a-zA-Z0-9_.-]+)?$' || [ "${#version}" -gt 128 ]; then
  echo 'Chart appVersion or release tag must be MAJOR.MINOR.PATCH with an optional -prerelease suffix (tags may start with v).' >&2
  exit 1
fi

# Keep credentials out of the checkout and remove them when the job ends.
. "$repo_root/scripts/ci/buildkit.sh"
DOCKER_CONFIG=$(mktemp -d)
export DOCKER_CONFIG
trap 'tb_cleanup_builder; rm -rf "$DOCKER_CONFIG"' EXIT
printf '%s' "$password" | docker login "$registry" --username "$username" --password-stdin
unset password default_password
export BUILDX_NO_DEFAULT_ATTESTATIONS=1
tb_setup_builder

# Build every image before publishing any of them.
for component in backend frontend mcp network-scan device-info reboot; do
  case "$component" in
    backend|frontend) context="src/$component" ;;
    mcp) context=src/mcp-server ;;
    *) context="src/plugins/$component" ;;
  esac
  set --
  if [ "${GITLAB_CI:-}" = true ] && [ "$component" != frontend ] && [ -n "${TB_PIP_INDEX_URL:-}" ]; then
    # Pass the variable by reference, never as a build argument or URL in argv.
    set -- --secret id=pip_index_url,env=TB_PIP_INDEX_URL
    if [ -n "${TB_PIP_TRUSTED_HOST:-}" ]; then
      set -- "$@" --secret id=pip_trusted_host,env=TB_PIP_TRUSTED_HOST
    fi
  fi
  if [ "${GITLAB_CI:-}" = true ] && [ "$component" = frontend ] && [ -n "${TB_HTTP_PROXY:-}" ]; then
    # The frontend is the only image whose build fetches npm and Debian packages.
    set -- "$@" --secret id=http_proxy,env=TB_HTTP_PROXY
  fi
  tb_build --pull --build-arg "VERSION=$version" \
    "$@" \
    --label "org.opencontainers.image.source=$source_url" \
    --label "org.opencontainers.image.revision=$commit" \
    --tag "$prefix/$component:sha-$commit" "$repo_root/$context"
done
for component in backend frontend mcp network-scan device-info reboot; do
  image="$prefix/$component"
  docker push "$image:sha-$commit"
  docker tag "$image:sha-$commit" "$image:$version"
  docker push "$image:$version"
  if [ -z "$release_tag" ]; then
    docker tag "$image:sha-$commit" "$image:latest"
    docker push "$image:latest"
  fi
done
