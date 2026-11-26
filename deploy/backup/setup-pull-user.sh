#!/usr/bin/env bash
# Create the read-only SFTP user that off-server machines use to download
# backups, and authorize one public key for it.
#
# Usage (as root on the server):
#   setup-pull-user.sh 'ssh-ed25519 AAAA... bkpull@pos'
# Running it again with another key adds that key too.
#
# The user can only read /opt/backups over SFTP: no shell, no port
# forwarding, no writes, and it cannot see the rest of the filesystem.
set -euo pipefail

PUBKEY="${1:?usage: setup-pull-user.sh 'ssh-ed25519 AAAA... comment'}"
USER_NAME="bkpull"
BACKUP_DIR="/opt/backups"
HOME_DIR="/var/lib/$USER_NAME"
SSHD_SNIPPET="/etc/ssh/sshd_config.d/90-$USER_NAME.conf"

case "$PUBKEY" in
    ssh-ed25519\ *) ;;
    *) echo "expected an ssh-ed25519 public key" >&2; exit 1 ;;
esac

# 1. System user with no shell. Its home and keys belong to root, so the
#    user cannot change its own authorized keys.
getent group "$USER_NAME" > /dev/null || groupadd --system "$USER_NAME"
id "$USER_NAME" > /dev/null 2>&1 || useradd --system --gid "$USER_NAME" \
    --home-dir "$HOME_DIR" --no-create-home --shell /usr/sbin/nologin "$USER_NAME"
install -d -o root -g root -m 755 "$HOME_DIR" "$HOME_DIR/.ssh"
keys="$HOME_DIR/.ssh/authorized_keys"
touch "$keys"
grep -qxF "restrict $PUBKEY" "$keys" || echo "restrict $PUBKEY" >> "$keys"
chown root:root "$keys"
chmod 644 "$keys"

# 2. The backup folder becomes the user's whole visible filesystem (chroot).
#    sshd requires it to be owned by root and not writable by anyone else.
install -d -o root -g "$USER_NAME" -m 750 "$BACKUP_DIR"
find "$BACKUP_DIR" -maxdepth 1 -type f -name 'backup-*' -exec chgrp "$USER_NAME" {} +

# 3. sshd rules for this user only. "-R" makes the SFTP server read-only.
cat > "$SSHD_SNIPPET.new" <<EOF
# Read-only SFTP access to $BACKUP_DIR for backup downloads.
Match User $USER_NAME
    ForceCommand internal-sftp -R
    ChrootDirectory $BACKUP_DIR
    AuthenticationMethods publickey
    AllowTcpForwarding no
    AllowAgentForwarding no
    AllowStreamLocalForwarding no
    PermitTunnel no
    X11Forwarding no
    PermitTTY no
EOF
mv "$SSHD_SNIPPET.new" "$SSHD_SNIPPET"

# 4. Validate before reloading, and check that nobody else is affected.
if ! sshd -t; then
    rm -f "$SSHD_SNIPPET"
    echo "sshd config test failed; snippet removed, nothing reloaded" >&2
    exit 1
fi
root_cmd=$(sshd -T -C user=root,host=check,addr=127.0.0.1 | awk '$1=="forcecommand"{print $2}')
pull_cmd=$(sshd -T -C user=$USER_NAME,host=check,addr=127.0.0.1 | awk '$1=="chrootdirectory"{print $2}')
if [ "${root_cmd:-none}" != "none" ] || [ "$pull_cmd" != "$BACKUP_DIR" ]; then
    rm -f "$SSHD_SNIPPET"
    echo "unexpected sshd result (root forcecommand=$root_cmd, $USER_NAME chroot=$pull_cmd); snippet removed" >&2
    exit 1
fi
systemctl reload ssh
echo "OK: $USER_NAME can read $BACKUP_DIR over SFTP; root login unchanged."
