#!/usr/bin/env bash
# Install the ticker as a systemd service on the host wired to the LED board.
#
# The unit runs .venv/bin/python directly rather than `uv run`, because the unit's
# ProtectHome=true makes uv's cache unreachable at runtime. So the venv has to exist
# before the service starts -- that is what this script is for.
#
#   sudo ./deploy/install.sh
#
# Re-running it is safe: it updates the code in place and restarts the service.

set -euo pipefail

PREFIX="${PREFIX:-/opt/nhl-score-ticker}"
SERVICE_USER="${SERVICE_USER:-nhl}"
UNIT=/etc/systemd/system/nhl-ticker.service
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

if [[ $EUID -ne 0 ]]; then
  echo "run with sudo" >&2
  exit 1
fi

if ! id -u "$SERVICE_USER" >/dev/null 2>&1; then
  echo "==> creating user $SERVICE_USER"
  useradd --system --create-home --shell /usr/sbin/nologin "$SERVICE_USER"
fi
# Goal horns need the audio device.
usermod -aG audio "$SERVICE_USER" || true

echo "==> installing source to $PREFIX"
mkdir -p "$PREFIX"
cp -r "$REPO_ROOT/engine" "$PREFIX/"
# The built UI is optional; the engine serves it when present.
[[ -d "$REPO_ROOT/ui/dist" ]] && mkdir -p "$PREFIX/ui" && cp -r "$REPO_ROOT/ui/dist" "$PREFIX/ui/"

echo "==> building the virtualenv"
# Built here, at install time, where HOME is available -- not at service start, where the
# unit's sandboxing hides it.
cd "$PREFIX/engine"
if command -v uv >/dev/null 2>&1; then
  uv venv .venv
  VIRTUAL_ENV="$PWD/.venv" uv pip install --python .venv/bin/python -e '.[horn]'
else
  python3 -m venv .venv
  .venv/bin/pip install --upgrade pip
  .venv/bin/pip install -e '.[horn]'
fi

if [[ ! -f "$PREFIX/engine/.env" ]]; then
  echo "==> seeding .env from .env.example (edit it: board IP, timezone, sinks)"
  cp "$PREFIX/engine/.env.example" "$PREFIX/engine/.env"
fi

chown -R "$SERVICE_USER:$SERVICE_USER" "$PREFIX"

echo "==> installing the unit"
cp "$REPO_ROOT/deploy/nhl-ticker.service" "$UNIT"
systemctl daemon-reload
systemctl enable nhl-ticker
systemctl restart nhl-ticker

echo
echo "installed. next:"
echo "  sudo -e $PREFIX/engine/.env        # board IP, TICKER_TIMEZONE, enable sinks"
echo "  sudo systemctl restart nhl-ticker"
echo "  journalctl -u nhl-ticker -f"
