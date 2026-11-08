#!/bin/sh
# GitHub Actions chart publisher: pushes helm/testbench to GHCR as an OCI chart.
set -eu

if [ "${GITHUB_ACTIONS:-}" != true ]; then
  echo 'Run this chart publisher through GitHub Actions.' >&2
  exit 1
fi
command -v helm >/dev/null 2>&1 || { echo 'helm is required on the runner.' >&2; exit 1; }

registry=ghcr.io
project=$(printf '%s' "$GITHUB_REPOSITORY" | tr '[:upper:]' '[:lower:]')
repository="$registry/$project/charts"
: "${GITHUB_TOKEN:?Registry password is required}"

repo_root=$(CDPATH= cd -- "$(dirname -- "$0")/../.." && pwd)
chart_dir="$repo_root/helm/testbench"
name=$(sed -n 's/^name: *\([^ ]*\) *$/\1/p' "$chart_dir/Chart.yaml")
version=$(sed -n 's/^version: *"\{0,1\}\([^" ]*\)"\{0,1\} *$/\1/p' "$chart_dir/Chart.yaml")
if ! printf '%s\n' "$version" | LC_ALL=C grep -Eq '^[0-9]+\.[0-9]+\.[0-9]+(-[a-zA-Z0-9_.-]+)?$' || [ "${#version}" -gt 128 ]; then
  echo 'Chart version must be MAJOR.MINOR.PATCH with an optional -prerelease suffix.' >&2
  exit 1
fi
# A release tag must publish exactly the chart version it was cut from.
if [ "${GITHUB_REF_TYPE:-}" = tag ] && [ "${GITHUB_REF_NAME#v}" != "$version" ]; then
  echo "Release tag $GITHUB_REF_NAME does not match chart version $version." >&2
  exit 1
fi

# Keep credentials and Helm state out of the checkout; remove them at exit.
work=$(mktemp -d)
trap 'rm -rf "$work"' EXIT
export HELM_REGISTRY_CONFIG="$work/registry.json"
export HELM_CACHE_HOME="$work/cache" HELM_CONFIG_HOME="$work/config" HELM_DATA_HOME="$work/data"
printf '%s' "$GITHUB_TOKEN" | helm registry login "$registry" --username "$GITHUB_ACTOR" --password-stdin
unset GITHUB_TOKEN

# Published chart versions are immutable: consumers pin them, and Flux's
# ChartVersion strategy never re-pulls a version it already has.
if helm show chart "oci://$repository/$name" --version "$version" >/dev/null 2>&1; then
  echo "oci://$repository/$name:$version is already published; leaving it unchanged."
  exit 0
fi

helm package "$chart_dir" --destination "$work"
helm push "$work/$name-$version.tgz" "oci://$repository"
echo "Published oci://$repository/$name:$version"
