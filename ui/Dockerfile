FROM nginx:1.27-alpine

COPY templates/default.conf.template /etc/nginx/templates/default.conf.template
COPY html /usr/share/nginx/html

# The nginx image runs envsubst over /etc/nginx/templates at startup. Without
# this filter it would also eat nginx's own $host / $remote_addr variables.
ENV NGINX_ENVSUBST_FILTER=API_UPSTREAM \
    API_UPSTREAM=api:8000

EXPOSE 80
