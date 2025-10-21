#!/usr/bin/env bash
# mirrorhub/scripts/setup_nginx_json_log.sh

set -euo pipefail
INC="/root/mirrorhub/nginx/base_json_log.conf"

apt-get update
apt-get install -y nginx

if ! grep -q "$INC" /etc/nginx/nginx.conf; then
  sed -i "/http {/a \    include $INC;" /etc/nginx/nginx.conf
fi

nginx -t && systemctl reload nginx
echo "OK"