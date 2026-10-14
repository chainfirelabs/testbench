FROM python:3.13-slim-trixie AS build
WORKDIR /build
COPY pyproject.toml ./
COPY replication_plugin ./replication_plugin
RUN --mount=type=secret,id=pip_index_url,env=PIP_INDEX_URL \
    --mount=type=secret,id=pip_trusted_host,env=PIP_TRUSTED_HOST \
    pip install --no-cache-dir --target /opt/python .

FROM gcr.io/distroless/python3-debian13:nonroot
ENV PYTHONPATH=/opt/python PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1
WORKDIR /app
COPY --from=build /opt/python /opt/python
USER 65532:65532
EXPOSE 8080
ENTRYPOINT ["/usr/bin/python3", "-m", "uvicorn", "replication_plugin.server:app", "--host", "0.0.0.0", "--port", "8080"]
