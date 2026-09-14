# Match the Python ABI and Debian release of the distroless runtime.
FROM python:3.13-slim-trixie AS build
WORKDIR /build
COPY pyproject.toml ./
COPY tb_mcp ./tb_mcp
# Optional CI-only index override; the URL is never stored in an image layer.
RUN --mount=type=secret,id=pip_index_url,env=PIP_INDEX_URL \
    --mount=type=secret,id=pip_trusted_host,env=PIP_TRUSTED_HOST \
    pip install --no-cache-dir --target /opt/python .

FROM gcr.io/distroless/python3-debian13:nonroot
ARG VERSION=dev
ENV TB_MCP_VERSION=${VERSION}
LABEL org.opencontainers.image.title="testbench-mcp" \
      org.opencontainers.image.version="${VERSION}" \
      org.opencontainers.image.licenses="MIT"
ENV PYTHONPATH=/opt/python \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1
WORKDIR /app
COPY --from=build /opt/python /opt/python
COPY healthcheck.py ./
COPY LICENSE /usr/share/licenses/testbench/LICENSE
USER 65532:65532
EXPOSE 8003
ENTRYPOINT ["/usr/bin/python3"]
CMD ["-m", "tb_mcp"]
