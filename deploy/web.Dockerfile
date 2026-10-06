ARG NODE_BASE_IMAGE
ARG NGINX_BASE_IMAGE
FROM ${NODE_BASE_IMAGE} AS build
WORKDIR /workspace
ENV VITE_API_BASE_URL="" \
    PLAYWRIGHT_SKIP_BROWSER_DOWNLOAD=1
# This exact version comes from the unchanged root packageManager declaration.
RUN node -e "if(process.versions.node.split('.')[0]!=='24')throw Error('Node24 required')" && npm install --global pnpm@11.19.0 --ignore-scripts && test "$(pnpm --version)" = "11.19.0"
COPY package.json pnpm-lock.yaml pnpm-workspace.yaml ./
COPY apps/web/ ./apps/web/
COPY packages/contracts/ ./packages/contracts/
ARG BF_PNPM_LOCK_SHA256
RUN node -e "const fs=require('node:fs'),crypto=require('node:crypto');if(crypto.createHash('sha256').update(fs.readFileSync('pnpm-lock.yaml')).digest('hex')!==process.argv[1])throw Error('Original lock SHA differs')" "$BF_PNPM_LOCK_SHA256"
# Linux native esbuild/oxide packages are installed inside the Linux stage, under the lock.
# Preserve pnpm-workspace.yaml allowBuilds; never copy Windows node_modules/store.
RUN pnpm install --frozen-lockfile
RUN pnpm --dir apps/web build

FROM ${NGINX_BASE_IMAGE} AS runtime
COPY deploy/nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=build /workspace/apps/web/dist/ /usr/share/nginx/html/
ARG BF_SOURCE_HEAD
ARG BF_SOURCE_DIGEST
ARG BF_PNPM_LOCK_SHA256
LABEL org.opencontainers.image.revision=$BF_SOURCE_HEAD \
      io.bounded-funds.source-digest=$BF_SOURCE_DIGEST \
      io.bounded-funds.pnpm-lock-sha256=$BF_PNPM_LOCK_SHA256 \
      io.bounded-funds.purpose=ISOLATED_SIMULATED_DEMO
EXPOSE 80
CMD ["nginx", "-g", "daemon off;"]
