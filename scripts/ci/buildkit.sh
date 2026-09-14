# Sourced by build scripts after repo_root is set. No changes to the default builder.
tb_builder=""
tb_context=""

tb_cleanup_builder() {
  if [ -n "$tb_builder" ]; then
    docker buildx rm "$tb_builder" >/dev/null 2>&1 || true
  fi
  if [ -n "$tb_context" ]; then
    docker context rm -f "$tb_context" >/dev/null 2>&1 || true
  fi
}

tb_setup_builder() {
  # GitLab can supply a private File variable; public builds need no config.
  tb_config=${TB_BUILDKIT_CONFIG:-}
  case "$tb_config" in ''|direct) return 0 ;; esac
  if [ ! -f "$tb_config" ]; then
    echo "BuildKit configuration not found: $tb_config" >&2
    return 1
  fi
  : "${TB_BUILDKIT_IMAGE:?Set TB_BUILDKIT_IMAGE to a reachable BuildKit image when using mirrors}"
  if [ -n "${TB_MIRROR_USER:-}" ] || [ -n "${TB_MIRROR_PASSWORD:-}" ]; then
    : "${TB_MIRROR_REGISTRY:?Set the mirror registry hostname}"
    : "${TB_MIRROR_USER:?Set both mirror credentials}"
    : "${TB_MIRROR_PASSWORD:?Set both mirror credentials}"
    printf '%s' "$TB_MIRROR_PASSWORD" | docker login "$TB_MIRROR_REGISTRY" \
      --username "$TB_MIRROR_USER" --password-stdin
  fi
  set --
  if [ -n "${DOCKER_HOST:-}" ] && { [ -n "${DOCKER_TLS_VERIFY:-}" ] || [ -n "${DOCKER_TLS:-}" ]; }; then
    # Capture the current endpoint and TLS material without changing the active
    # context. Buildx cannot persist TLS supplied only through environment vars.
    tb_context="testbench-tls-$$"
    docker context create "$tb_context"
    set -- "$tb_context"
  fi
  tb_builder="testbench-$$"
  docker buildx create --name "$tb_builder" --driver docker-container \
    --driver-opt "image=$TB_BUILDKIT_IMAGE" \
    --buildkitd-config "$tb_config" --bootstrap "$@"
}

tb_build() {
  if [ -n "$tb_builder" ]; then
    docker buildx build --builder "$tb_builder" --load "$@"
  else
    docker build "$@"
  fi
}
