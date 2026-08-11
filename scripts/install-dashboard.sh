#!/bin/sh
set -eu

DOMAIN=arena.911439925.xyz
INSTALL_ROOT=${ARENA_INSTALL_ROOT:-/opt/arena-hero-agent}
SYSTEMD_UNIT_DIR=${ARENA_SYSTEMD_UNIT_DIR:-/etc/systemd/system}
CADDY_CONFIG=${ARENA_CADDY_CONFIG:-/etc/caddy/Caddyfile}
CADDY_SNIPPET_DIR=${ARENA_CADDY_SNIPPET_DIR:-/etc/caddy/Caddyfile.d}
CADDY_ENV=${ARENA_CADDY_ENV:-/etc/arena-hero-dashboard.env}
PASSWORD_HASH_FILE=

usage() {
    cat <<'EOF'
Usage: sudo sh scripts/install-dashboard.sh --password-hash-file PATH

Install the read-only Dashboard edge configuration. PATH must contain one Caddy
bcrypt hash generated from a password that was never passed on a command line.
EOF
}

while [ "$#" -gt 0 ]; do
    case "$1" in
        --password-hash-file)
            [ "$#" -ge 2 ] || { echo "--password-hash-file requires a path" >&2; exit 2; }
            PASSWORD_HASH_FILE=$2
            shift 2
            ;;
        -h|--help)
            usage
            exit 0
            ;;
        *)
            echo "Unknown option: $1" >&2
            usage >&2
            exit 2
            ;;
    esac
done

[ "$(id -u)" -eq 0 ] || { echo "Run this installer as root." >&2; exit 2; }
[ -n "$PASSWORD_HASH_FILE" ] || { usage >&2; exit 2; }
[ -r "$PASSWORD_HASH_FILE" ] || { echo "Password hash file is not readable." >&2; exit 2; }
for command_name in caddy chmod chown curl dirname getent grep install mktemp mv sed systemctl tr; do
    command -v "$command_name" >/dev/null 2>&1 || {
        echo "Required command is unavailable: $command_name" >&2
        exit 2
    }
done
getent passwd caddy >/dev/null 2>&1 || { echo "The caddy service account is missing." >&2; exit 2; }
[ -x "$INSTALL_ROOT/current/.venv/bin/arena-hero-dashboard" ] || {
    echo "Deploy a release containing arena-hero-dashboard first." >&2
    exit 2
}
[ -r "$INSTALL_ROOT/current/dashboard/index.html" ] || {
    echo "The deployed Dashboard frontend is missing." >&2
    exit 2
}

password_hash=$(sed -n '1p' "$PASSWORD_HASH_FILE" | tr -d '\r\n')
case "$password_hash" in
    '$2a$'*|'$2b$'*|'$2y$'*) ;;
    *) echo "Password hash file must contain one bcrypt hash." >&2; exit 2 ;;
esac
[ "${#password_hash}" -eq 60 ] || { echo "Unexpected bcrypt hash length." >&2; exit 2; }

temporary=$(mktemp "$(dirname "$CADDY_ENV")/.arena-dashboard.env.XXXXXX")
cleanup() {
    if [ -n "$temporary" ]; then
        rm -f "$temporary"
    fi
}
trap cleanup EXIT HUP INT TERM
printf "ARENA_DASHBOARD_PASSWORD_HASH='%s'\n" "$password_hash" > "$temporary"
chown root:caddy "$temporary"
chmod 0640 "$temporary"
mv -f "$temporary" "$CADDY_ENV"
temporary=

install -d -o root -g caddy -m 0750 "$CADDY_SNIPPET_DIR"
install -o root -g caddy -m 0640 \
    "$(dirname "$0")/../deploy/arena-hero-dashboard.caddy" \
    "$CADDY_SNIPPET_DIR/arena-hero-dashboard.caddy"
if ! grep -qE '^[[:space:]]*import[[:space:]]+Caddyfile\.d/\*\.caddy[[:space:]]*$' "$CADDY_CONFIG"; then
    printf '\nimport Caddyfile.d/*.caddy\n' >> "$CADDY_CONFIG"
fi

install -d -o root -g root -m 0755 "$SYSTEMD_UNIT_DIR/caddy.service.d"
printf '%s\n' \
    '[Service]' \
    "EnvironmentFile=$CADDY_ENV" \
    > "$SYSTEMD_UNIT_DIR/caddy.service.d/arena-dashboard.conf"
chmod 0644 "$SYSTEMD_UNIT_DIR/caddy.service.d/arena-dashboard.conf"

ARENA_DASHBOARD_PASSWORD_HASH=$password_hash caddy validate --config "$CADDY_CONFIG"
unset password_hash
systemctl daemon-reload
systemctl enable --now arena-hero-dashboard.service
systemctl enable --now caddy
systemctl reload caddy
sleep 3
curl --fail --silent --show-error http://127.0.0.1:8765/api/v1/health >/dev/null
systemctl is-active --quiet arena-hero-dashboard.service
systemctl is-active --quiet caddy

echo "Arena Hero Dashboard installed for https://$DOMAIN."
echo "The backend remains bound to 127.0.0.1:8765."
