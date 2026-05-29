# /etc/nginx/conf.d/fapiao_proxy_params.inc
# Shared proxy directives.

proxy_http_version 1.1;
proxy_set_header Host              $host;
proxy_set_header X-Real-IP         $remote_addr;
proxy_set_header X-Forwarded-For   $proxy_add_x_forwarded_for;
proxy_set_header X-Forwarded-Proto $scheme;
proxy_set_header X-Forwarded-Host  $host;

# Long-lived requests (sync may take ~30s)
proxy_read_timeout 120s;
proxy_send_timeout 60s;
proxy_connect_timeout 5s;

# Don't buffer streamed responses (CSV export)
proxy_buffering off;
