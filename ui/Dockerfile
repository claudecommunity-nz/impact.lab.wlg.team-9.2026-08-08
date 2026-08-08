FROM nginx:1.27-alpine

COPY templates/default.conf.template /etc/nginx/templates/default.conf.template
COPY html /usr/share/nginx/html

# The nginx image runs envsubst over /etc/nginx/templates at startup. Without
# this filter it would also eat nginx's own $host / $remote_addr variables.
# It is an anchored regex, so both names have to be listed explicitly.
ENV NGINX_ENVSUBST_FILTER="(API_UPSTREAM|UI_LISTEN_PORT)" \
    API_UPSTREAM=api:8000 \
    UI_LISTEN_PORT=80

EXPOSE 80
