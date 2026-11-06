#!/usr/bin/env bash
# Idempotent bootstrap: Docker Engine + Compose plugin + NVIDIA Container Toolkit
# Run inside Ubuntu-24.04 WSL as a sudoer. Does not delete volumes.
set -euo pipefail

log() { printf '[bootstrap] %s\n' "$*"; }

if [[ "$(id -u)" -eq 0 ]]; then
  log "Do not run as root; use a sudo user so docker group applies."
  exit 1
fi

export DEBIAN_FRONTEND=noninteractive

log "OS: $(. /etc/os-release && echo "$PRETTY_NAME")"
log "Disk / :"
df -h /

# Prefer Linux Docker Engine over Windows Docker Desktop stubs on PATH (/mnt/c/...).
export PATH="/usr/bin:/bin:/usr/sbin:/sbin:${PATH}"

has_linux_docker() {
  [[ -x /usr/bin/docker ]] && /usr/bin/docker --version 2>/dev/null | grep -qi docker
}

# --- Docker Engine (official Ubuntu repo) ---
if ! has_linux_docker; then
  log "Installing Docker Engine (Linux)..."
  # Drop Desktop stub early so installs are unambiguous
  if [[ -d "/mnt/c/Program Files/Docker/Docker/resources/bin" ]]; then
    log "NOTE: Windows Docker Desktop bin is on PATH; using /usr/bin/docker instead."
  fi
  sudo apt-get update -y
  sudo apt-get install -y ca-certificates curl gnupg
  sudo install -m 0755 -d /etc/apt/keyrings
  if [[ ! -f /etc/apt/keyrings/docker.asc ]]; then
    sudo curl -fsSL https://download.docker.com/linux/ubuntu/gpg -o /etc/apt/keyrings/docker.asc
    sudo chmod a+r /etc/apt/keyrings/docker.asc
  fi
  . /etc/os-release
  echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.asc] https://download.docker.com/linux/ubuntu ${VERSION_CODENAME} stable" \
    | sudo tee /etc/apt/sources.list.d/docker.list >/dev/null
  sudo apt-get update -y
  sudo apt-get install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
else
  log "Linux Docker already present: $(/usr/bin/docker --version)"
fi

# Ensure docker group exists (created by docker-ce packages)
if getent group docker >/dev/null 2>&1; then
  sudo usermod -aG docker "$USER" || true
fi

# Enable/start via systemd if available
if command -v systemctl >/dev/null 2>&1; then
  sudo systemctl enable --now docker || true
fi

# --- NVIDIA Container Toolkit ---
if ! command -v nvidia-ctk >/dev/null 2>&1; then
  log "Installing NVIDIA Container Toolkit..."
  curl -fsSL https://nvidia.github.io/libnvidia-container/gpgkey \
    | sudo gpg --dearmor -o /usr/share/keyrings/nvidia-container-toolkit-keyring.gpg
  curl -s -L https://nvidia.github.io/libnvidia-container/stable/deb/nvidia-container-toolkit.list \
    | sed 's#deb https://#deb [signed-by=/usr/share/keyrings/nvidia-container-toolkit-keyring.gpg] https://#g' \
    | sudo tee /etc/apt/sources.list.d/nvidia-container-toolkit.list >/dev/null
  sudo apt-get update -y
  sudo apt-get install -y nvidia-container-toolkit
else
  log "nvidia-ctk already present: $(nvidia-ctk --version 2>/dev/null || true)"
fi

if has_linux_docker && command -v nvidia-ctk >/dev/null 2>&1; then
  sudo nvidia-ctk runtime configure --runtime=docker
  if command -v systemctl >/dev/null 2>&1; then
    sudo systemctl restart docker || true
  else
    sudo service docker restart || true
  fi
fi

log "docker version:"
sudo /usr/bin/docker version || true
log "compose version:"
sudo /usr/bin/docker compose version || true
log "Docker Root Dir:"
sudo /usr/bin/docker info 2>/dev/null | sed -n 's/.*Docker Root Dir: //p' || true

log "Bootstrap finished. Re-enter WSL shell (or new session) so group 'docker' applies without sudo."
log "Next: sudo /usr/bin/docker run --rm --gpus all nvidia/cuda:12.6.0-base-ubuntu24.04 nvidia-smi"
