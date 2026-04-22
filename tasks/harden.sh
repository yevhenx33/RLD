#!/usr/bin/env bash
# =============================================================================
# VPS Hardening Script — vps-39d1f9f2
# Run sections ONE AT A TIME. This is NOT a fire-and-forget script.
# Each section is idempotent (safe to re-run).
# =============================================================================
set -euo pipefail

# ─── SECTION 1: Install + configure fail2ban (C1) ─────────────────────────────
section_fail2ban() {
  echo "[+] Installing fail2ban..."
  sudo apt-get install -y fail2ban

  sudo tee /etc/fail2ban/jail.local > /dev/null <<'EOF'
[DEFAULT]
bantime  = 1h
findtime = 10m
maxretry = 5
backend  = systemd

[sshd]
enabled  = true
port     = 22
maxretry = 3
bantime  = 24h
EOF

  sudo systemctl enable fail2ban
  sudo systemctl restart fail2ban
  echo "[+] fail2ban status:"
  sudo fail2ban-client status sshd
}

# ─── SECTION 2: Fix Docker/UFW bypass — bind ports to 127.0.0.1 (C2, H1, H3) ──
# Canonical targets:
#   - docker/reth/docker-compose.reth.yml
#   - docker/docker-compose.infra.yml
#   - docker/docker-compose.frontend.yml
# Keep legacy compose files deprecated.
section_docker_port_binding() {
  echo "[!] Manual action required in canonical compose files under docker/:"
  echo ""
  echo "  CHANGE:"
  echo "    ports:"
  echo "      - '5432:5432'"
  echo "  TO:"
  echo "    ports:"
  echo "      - '127.0.0.1:5432:5432'"
  echo ""
  echo "  Same for the indexer (8080):"
  echo "    ports:"
  echo "      - '127.0.0.1:8080:8080'"
  echo ""
  echo "  After editing, run:"
  echo "    bash ~/RLD/docker/scripts/stack.sh restart"

  # Create hardened daemon.json
  echo "[+] Writing /etc/docker/daemon.json..."
  sudo tee /etc/docker/daemon.json > /dev/null <<'EOF'
{
  "iptables": true,
  "userland-proxy": false,
  "live-restore": true,
  "log-driver": "json-file",
  "log-opts": {
    "max-size": "50m",
    "max-file": "3"
  },
  "no-new-privileges": true
}
EOF
  echo "[+] Restart Docker to apply:"
  echo "    sudo systemctl restart docker"
}

# ─── SECTION 3: Harden sshd_config (M2, M3) ──────────────────────────────────
section_sshd_harden() {
  SSHD_CFG=/etc/ssh/sshd_config

  echo "[+] Hardening sshd_config..."

  # Disable X11 forwarding
  sudo sed -i 's/^X11Forwarding yes/X11Forwarding no/' "$SSHD_CFG"

  # Add hardened settings (idempotent via grep check)
  declare -A SETTINGS=(
    ["MaxAuthTries"]="3"
    ["LoginGraceTime"]="20"
    ["MaxSessions"]="3"
    ["AllowAgentForwarding"]="no"
    ["AllowTcpForwarding"]="no"
    ["PermitEmptyPasswords"]="no"
    ["PermitRootLogin"]="no"
  )

  for key in "${!SETTINGS[@]}"; do
    val="${SETTINGS[$key]}"
    if grep -q "^$key" "$SSHD_CFG"; then
      sudo sed -i "s/^$key.*/$key $val/" "$SSHD_CFG"
    else
      echo "$key $val" | sudo tee -a "$SSHD_CFG" > /dev/null
    fi
  done

  sudo sshd -t && echo "[+] sshd config valid. Restarting..."
  sudo systemctl restart ssh
  echo "[+] SSH hardened."
}

# ─── SECTION 4: Kernel sysctl hardening (M1) ─────────────────────────────────
section_sysctl() {
  echo "[+] Writing /etc/sysctl.d/99-hardening.conf..."
  sudo tee /etc/sysctl.d/99-hardening.conf > /dev/null <<'EOF'
# Disable sending ICMP redirects
net.ipv4.conf.all.send_redirects = 0
net.ipv4.conf.default.send_redirects = 0

# Strict reverse path filtering
net.ipv4.conf.all.rp_filter = 1
net.ipv4.conf.default.rp_filter = 1

# Ignore ICMP redirects
net.ipv4.conf.all.accept_redirects = 0
net.ipv4.conf.default.accept_redirects = 0
net.ipv6.conf.all.accept_redirects = 0

# SYN flood protection (already on, being explicit)
net.ipv4.tcp_syncookies = 1

# Ignore bogus ICMP error responses
net.ipv4.icmp_ignore_bogus_error_responses = 1

# Log martian packets
net.ipv4.conf.all.log_martians = 1
EOF
  sudo sysctl --system
  echo "[+] sysctl hardened."
}

# ─── SECTION 5: Remove NOPASSWD sudo (C4) ────────────────────────────────────
section_sudo_harden() {
  echo "[!] DANGER ZONE — Only run this if you are certain you have another"
  echo "    way to gain root (e.g., VPS console access via provider panel)."
  echo ""
  echo "    This will require a password for sudo operations."
  echo "    Removing NOPASSWD from /etc/sudoers.d/90-cloud-init-users..."
  echo ""
  read -rp "Are you sure? Type YES to proceed: " confirm
  if [[ "$confirm" == "YES" ]]; then
    sudo sed -i 's/ubuntu ALL=(ALL) NOPASSWD:ALL/ubuntu ALL=(ALL:ALL) ALL/' \
      /etc/sudoers.d/90-cloud-init-users
    echo "[+] NOPASSWD removed. Set a strong password: sudo passwd ubuntu"
  else
    echo "[~] Skipped."
  fi
}

# ─── SECTION 6: Remove ubuntu from lxd group (M4) ───────────────────────────
section_lxd_group() {
  echo "[+] Removing ubuntu from lxd group..."
  sudo gpasswd -d ubuntu lxd
  echo "[+] Done. Effective on next login."
}

# ─── SECTION 7: Apply pending security updates (H4) ─────────────────────────
section_apt_upgrade() {
  echo "[+] Running apt upgrade (52 pending packages)..."
  sudo apt-get update -qq
  sudo DEBIAN_FRONTEND=noninteractive apt-get upgrade -y
  echo "[+] Done. Check if a reboot is needed:"
  [ -f /var/run/reboot-required ] && echo "  ⚠️  REBOOT REQUIRED" || echo "  ✓ No reboot needed"
}

# ─── SECTION 8: Restrict 8090 dashboard to trusted IPs in UFW (H1) ───────────
section_restrict_dashboard() {
  echo "[!] Replace YOUR_IP with your actual public IP."
  echo ""
  echo "    sudo ufw delete allow 8090/tcp"
  echo "    sudo ufw allow from YOUR_IP to any port 8090 proto tcp"
  echo "    sudo ufw reload"
}

# ─── SECTION 9: Scope github-actions-deploy key (M6) ────────────────────────
# Add command= restriction to authorized_keys so the deploy key can only
# run specific commands (e.g., git operations or a deploy script)
section_scope_deploy_key() {
  echo "[!] Manual action: edit ~/.ssh/authorized_keys"
  echo ""
  echo "  CHANGE:"
  echo "    ssh-ed25519 AAAA... github-actions-deploy"
  echo "  TO:"
  echo '    command="/home/ubuntu/RLD/docker/scripts/deploy.sh",no-agent-forwarding,no-x11-forwarding,no-pty ssh-ed25519 AAAA... github-actions-deploy'
}

# =============================================================================
# ENTRY POINT
# =============================================================================
echo "Usage: bash harden.sh <section>"
echo "  Sections: fail2ban | docker_ports | sshd | sysctl | sudo | lxd | apt_upgrade | dashboard | deploy_key"

case "${1:-}" in
  fail2ban)       section_fail2ban ;;
  docker_ports)   section_docker_port_binding ;;
  sshd)           section_sshd_harden ;;
  sysctl)         section_sysctl ;;
  sudo)           section_sudo_harden ;;
  lxd)            section_lxd_group ;;
  apt_upgrade)    section_apt_upgrade ;;
  dashboard)      section_restrict_dashboard ;;
  deploy_key)     section_scope_deploy_key ;;
  *)
    echo ""
    echo "⚠️  START HERE (TODAY):"
    echo "  1. Rotate Alchemy key + Telegram token manually (see audit report)"
    echo "  2. bash harden.sh fail2ban"
    echo "  3. bash harden.sh docker_ports  (requires docker-compose edit)"
    echo ""
    echo "  THIS WEEK:"
    echo "  4. bash harden.sh sudo"
    echo "  5. bash harden.sh apt_upgrade"
    echo "  6. bash harden.sh dashboard"
    echo "  7. bash harden.sh sshd"
    echo ""
    echo "  THIS MONTH:"
    echo "  8.  bash harden.sh sysctl"
    echo "  9.  bash harden.sh lxd"
    echo "  10. bash harden.sh deploy_key"
    ;;
esac
