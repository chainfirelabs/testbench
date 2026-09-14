FROM node:22-alpine AS build
WORKDIR /app
COPY package*.json ./
# Optional per-command settings: no proxy URL is written to npm configuration.
RUN --mount=type=secret,id=http_proxy,env=npm_config_proxy \
    --mount=type=secret,id=http_proxy,env=npm_config_https_proxy \
    npm ci
COPY . .
ARG VERSION=dev
RUN VITE_APP_VERSION="$VERSION" npm run build
# Keep the complete notices for packages that remain after development-only
# build tooling is removed. The application bundle is not a substitute for
# the license text required by those packages.
RUN npm prune --omit=dev \
    && { find node_modules -type f \( -iname 'LICENSE*' -o -iname 'NOTICE*' \) \
         | sort \
         | while IFS= read -r license; do \
             printf '\n===== %s =====\n' "$license"; \
             cat "$license"; \
           done; \
       } > /app/THIRD_PARTY_LICENSES.txt

# Copy nginx and only its linked libraries from the same Debian release.
FROM debian:trixie-slim AS nginx
# APT honors lowercase proxy variables; absent secrets leave direct access.
RUN --mount=type=secret,id=http_proxy,env=http_proxy \
    --mount=type=secret,id=http_proxy,env=https_proxy \
    apt-get update && apt-get install -y --no-install-recommends nginx-light \
    && mkdir /runtime \
    && ldd /usr/sbin/nginx | awk '/=> \// {print $3}' \
       | while read -r library; do \
           directory=$(readlink -f "$(dirname "$library")"); \
           cp -L --parents "$directory/$(basename "$library")" /runtime/; \
         done
# Preserve Debian copyright/license records for nginx and every shared library
# copied into the otherwise minimal final image.
RUN set -eu; \
    mkdir -p /runtime/usr/share/doc; \
    for file in /usr/sbin/nginx $(ldd /usr/sbin/nginx | awk '/=> \// {print $3}'); do \
      package=$(dpkg-query -S "$file" 2>/dev/null | sed -n '1s/: .*//p' || true); \
      if [ -z "$package" ]; then \
        resolved=$(readlink -f "$file"); \
        package=$(dpkg-query -S "$resolved" 2>/dev/null | sed -n '1s/: .*//p' || true); \
      fi; \
      package=${package%%:*}; \
      [ -z "$package" ] || [ ! -f "/usr/share/doc/$package/copyright" ] || { \
        mkdir -p "/runtime/usr/share/doc/$package"; \
        cp -L "/usr/share/doc/$package/copyright" "/runtime/usr/share/doc/$package/copyright"; \
      }; \
    done

FROM gcr.io/distroless/python3-debian13:nonroot
ARG VERSION=dev
LABEL org.opencontainers.image.title="testbench-frontend" \
      org.opencontainers.image.version="${VERSION}" \
      org.opencontainers.image.licenses="MIT"
ENV TB_HSTS_MAX_AGE="" PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1
COPY --from=nginx /runtime/ /
COPY --from=nginx /usr/sbin/nginx /usr/sbin/nginx
COPY --from=nginx /etc/nginx/mime.types /etc/nginx/mime.types
COPY nginx-main.conf /etc/nginx/nginx.conf
COPY nginx.conf /etc/nginx/templates/default.conf.template
COPY entrypoint.py /app/entrypoint.py
COPY --from=build /app/dist /usr/share/nginx/html
COPY --from=build /app/THIRD_PARTY_LICENSES.txt /usr/share/licenses/testbench/frontend-third-party.txt
COPY LICENSE /usr/share/licenses/testbench/LICENSE
USER 65532:65532
EXPOSE 8080
ENTRYPOINT ["/usr/bin/python3"]
CMD ["/app/entrypoint.py"]
