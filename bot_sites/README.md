# MirrorHub (SQLite)

## Установка

```bash
apt update
apt install -y python3-venv python3-pip nginx certbot python3-certbot-nginx
cd /root
git clone <this> mirrorhub
cd mirrorhub
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
python -c "from db.db import init_db; init_db()"
