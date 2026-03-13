FROM nginx:alpine
COPY . /usr/share/nginx/html
# Entrypoint script generates config.js from env vars at container start
COPY docker-entrypoint.d/40-env-config.sh /docker-entrypoint.d/40-env-config.sh
RUN chmod +x /docker-entrypoint.d/40-env-config.sh
EXPOSE 80
