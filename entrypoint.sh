#!/bin/bash
set -e

# 1. Hugging Face Space runtime environment configuration
export DATA_DB="${DATA_DB:-postgresql}"
export VECTOR_DB="${VECTOR_DB:-postgresql}"
export GRAPH_DB="${GRAPH_DB:-networkx}"
export CELERY_BACKEND="${CELERY_BACKEND:-redis}"
export PIPELINE_MODE="${PIPELINE_MODE:-lightweight}"

export MODELS_MODE="${MODELS_MODE:-local}"
export LLM_SMALL_PROVIDER="${LLM_SMALL_PROVIDER:-ollama}"
export LLM_LARGE_PROVIDER="${LLM_LARGE_PROVIDER:-ollama}"
export EMBEDDINGS_PROVIDER="${EMBEDDINGS_PROVIDER:-ollama}"
export OLLAMA_HOST="${OLLAMA_HOST:-127.0.0.1}"
export OLLAMA_PORT="${OLLAMA_PORT:-11434}"
export OLLAMA_MODELS="${OLLAMA_MODELS:-/opt/ollama-models}"
export OLLAMA_EMBEDDING_MODEL="${OLLAMA_EMBEDDING_MODEL:-qwen3-embedding:0.6b}"
export OLLAMA_LLM_SMALL_MODEL="${OLLAMA_LLM_SMALL_MODEL:-qwen3:0.6b}"
export OLLAMA_LLM_LARGE_MODEL="${OLLAMA_LLM_LARGE_MODEL:-qwen3:0.6b}"
export OLLAMA_NUM_PARALLEL="${OLLAMA_NUM_PARALLEL:-1}"
export OLLAMA_MAX_LOADED_MODELS="${OLLAMA_MAX_LOADED_MODELS:-1}"
export OLLAMA_NUM_THREADS="${OLLAMA_NUM_THREADS:-2}"
export OLLAMA_EMBEDDING_KEEP_ALIVE="${OLLAMA_EMBEDDING_KEEP_ALIVE:-10m}"
# Qwen3 Embedding 0.6B emits 1024-dimensional vectors.
for dimension in NODES TRIPLETS OBSERVATIONS DATA RELATIONSHIPS; do
    variable="EMBEDDING_${dimension}_DIMENSION"
    export "$variable=${!variable:-1024}"
done

export POSTGRES_HOST="${POSTGRES_HOST:-127.0.0.1}"
export POSTGRES_PORT="${POSTGRES_PORT:-5432}"
export POSTGRES_USERNAME="${POSTGRES_USERNAME:-brainapi}"
export POSTGRES_SYSTEM_DATABASE="${POSTGRES_SYSTEM_DATABASE:-brainapi}"
export POSTGRES_MAINTENANCE_DATABASE="${POSTGRES_MAINTENANCE_DATABASE:-postgres}"

export REDIS_HOST="${REDIS_HOST:-127.0.0.1}"
export REDIS_PORT="${REDIS_PORT:-6379}"

export DEFAULT_BRAIN_FALLBACK="${DEFAULT_BRAIN_FALLBACK:-false}"
export MCP_PORT="${MCP_PORT:-8001}"

# Persistent root determination
if [ -d "/data" ]; then
    PERSISTENT_ROOT="/data/brainapi"
else
    PERSISTENT_ROOT="/app/.data/brainapi"
    echo "WARNING: /data persistent storage unavailable; BrainAPI state will not survive container replacement/rebuild."
fi

mkdir -p "$PERSISTENT_ROOT/postgres" "$PERSISTENT_ROOT/redis" /var/lib/postgresql
chmod -R 777 "$PERSISTENT_ROOT" /var/lib/postgresql
chown -R postgres "$PERSISTENT_ROOT/postgres" /var/lib/postgresql 2>/dev/null || true

SECRETS_FILE="$PERSISTENT_ROOT/internal-secrets.env"
if [ ! -f "$SECRETS_FILE" ]; then
    PG_PASS="$(openssl rand -hex 16 2>/dev/null || date +%s%N | sha256sum | head -c 32)"
    RED_PASS="$(openssl rand -hex 16 2>/dev/null || date +%s%N | sha256sum | head -c 32)"
    cat <<EOF > "$SECRETS_FILE"
POSTGRES_PASSWORD=$PG_PASS
REDIS_PASSWORD=$RED_PASS
EOF
    chmod 0600 "$SECRETS_FILE"
fi

source "$SECRETS_FILE"
export POSTGRES_PASSWORD
export REDIS_PASSWORD

export CELERY_BROKER_URL="redis://:${REDIS_PASSWORD}@127.0.0.1:6379/0"
export CELERY_RESULT_BACKEND="redis://:${REDIS_PASSWORD}@127.0.0.1:6379/0"

# Print non-secret startup diagnostics
echo "=================================================="
echo "Starting BrainAPI Hugging Face Space Runtime"
echo "DATA_DB=$DATA_DB"
echo "VECTOR_DB=$VECTOR_DB"
echo "GRAPH_DB=$GRAPH_DB"
echo "POSTGRES_HOST=$POSTGRES_HOST"
echo "REDIS_HOST=$REDIS_HOST"
echo "persistent_root=$PERSISTENT_ROOT"
echo "=================================================="

# 2. PostgreSQL Setup
PGDATA="$PERSISTENT_ROOT/postgres"
export PGDATA
chown -R postgres "$PGDATA" /var/lib/postgresql 2>/dev/null || true

if [ ! -s "$PGDATA/PG_VERSION" ]; then
    su - postgres -c "/usr/lib/postgresql/15/bin/initdb -D '$PGDATA'"
    echo "listen_addresses = '127.0.0.1'" >> "$PGDATA/postgresql.conf"
fi

# 3. Redis Setup
cat <<EOF > /etc/redis/redis.conf
bind 127.0.0.1
port $REDIS_PORT
requirepass $REDIS_PASSWORD
dir $PERSISTENT_ROOT/redis
appendonly yes
appendfilename "appendonly.aof"
protected-mode no
EOF
chmod -R 777 "$PERSISTENT_ROOT/redis"

# Ensure legacy background instances are stopped before clean startup
su - postgres -c "/usr/lib/postgresql/15/bin/pg_ctl -D '$PGDATA' -m immediate stop" 2>/dev/null || true
pkill -9 redis-server 2>/dev/null || true
pkill -9 -f supervisord 2>/dev/null || true
sleep 1

# Start background services directly for temporary initialization
su - postgres -c "/usr/lib/postgresql/15/bin/pg_ctl -D '$PGDATA' -o '-k /tmp' -w start"
/usr/bin/redis-server /etc/redis/redis.conf --daemonize yes

echo "[entrypoint] Waiting for Postgres readiness check..."
until su - postgres -c "pg_isready -h 127.0.0.1 -p $POSTGRES_PORT"; do
    sleep 1
done

echo "[entrypoint] Ensuring Postgres database and extension..."
su - postgres -c "psql -h /tmp -d template1 -c 'CREATE EXTENSION IF NOT EXISTS vector;' 2>/dev/null || true"
su - postgres -c "psql -h /tmp -c \"CREATE USER $POSTGRES_USERNAME WITH PASSWORD '$POSTGRES_PASSWORD';\" 2>/dev/null || true"
su - postgres -c "psql -h /tmp -c \"ALTER USER $POSTGRES_USERNAME WITH PASSWORD '$POSTGRES_PASSWORD';\" 2>/dev/null || true"
su - postgres -c "psql -h /tmp -c \"CREATE DATABASE $POSTGRES_SYSTEM_DATABASE OWNER $POSTGRES_USERNAME;\" 2>/dev/null || true"
su - postgres -c "psql -h /tmp -d $POSTGRES_SYSTEM_DATABASE -c 'CREATE EXTENSION IF NOT EXISTS vector;' 2>/dev/null || true"
su - postgres -c "psql -h /tmp -c \"GRANT ALL PRIVILEGES ON DATABASE $POSTGRES_SYSTEM_DATABASE TO $POSTGRES_USERNAME;\" 2>/dev/null || true"
su - postgres -c "psql -h /tmp -c \"ALTER USER $POSTGRES_USERNAME CREATEDB;\" 2>/dev/null || true"

echo "[entrypoint] Waiting for Redis readiness check..."
until REDISCLI_AUTH="$REDIS_PASSWORD" redis-cli -h 127.0.0.1 -p $REDIS_PORT ping | grep -q PONG; do
    sleep 1
done

echo "[entrypoint] Verifying BrainAPI workspace bootstrap..."
if [ -z "${BRAINPAT_TOKEN:-}" ]; then
    echo "ERROR: BRAINPAT_TOKEN must be configured as a Hugging Face Space Secret." >&2
    exit 1
fi

/app/.venv/bin/python -c "
from src.services.data.main import data_adapter
from src.services.workspaces import WorkspaceService
WorkspaceService(data_adapter).bootstrap()
print('[entrypoint] Workspace bootstrap successful.')
"

# Stop temporary initialization daemons so Supervisord manages exactly one instance of each
echo "[entrypoint] Stopping temporary initialization daemons before handing control to Supervisord..."
su - postgres -c "/usr/lib/postgresql/15/bin/pg_ctl -D '$PGDATA' -m fast stop" 2>/dev/null || true
REDISCLI_AUTH="$REDIS_PASSWORD" redis-cli -h 127.0.0.1 -p $REDIS_PORT shutdown 2>/dev/null || true
sleep 1

# 4. Nginx Setup
if [ -f /app/deploy/nginx.conf ]; then
    cp /app/deploy/nginx.conf /etc/nginx/conf.d/default.conf
    rm -f /etc/nginx/sites-enabled/default 2>/dev/null || true
fi

# 5. Plugin setup
plugin_failure_policy="${PLUGIN_FAILURE_POLICY:-}"
if [ -z "$plugin_failure_policy" ]; then
    if [ "${ENV:-production}" = "development" ]; then
        plugin_failure_policy="warn"
    else
        plugin_failure_policy="fail"
    fi
fi

mkdir -p /app/plugins

if [ -n "$BRAINAPI_PLUGINS" ]; then
    IFS=',' read -ra PLUGINS <<< "$BRAINAPI_PLUGINS"
    for plugin_spec in "${PLUGINS[@]}"; do
        plugin_spec="$(echo "$plugin_spec" | xargs)"
        name="${plugin_spec%%:*}"
        version="${plugin_spec##*:}"
        [ "$name" = "$version" ] && version="latest"
        echo "[brainapi] Installing plugin: $name v$version"
        if ! /app/.venv/bin/python -m src.core.plugins.cli plugins install "$name" --version "$version"; then
            if [ "$plugin_failure_policy" = "fail" ]; then
                echo "[brainapi] Failed to install required plugin '$name'" >&2
                exit 1
            fi
            echo "[brainapi] WARNING: Failed to install plugin '$name'" >&2
        fi
    done
fi

# Copy supervisor config from deploy template
cp /app/deploy/supervisord.conf /etc/supervisor/supervisord.conf

echo "[entrypoint] Handing over execution to Supervisord (PID 1, nodaemon)..."
exec /usr/bin/supervisord -n -c /etc/supervisor/supervisord.conf
