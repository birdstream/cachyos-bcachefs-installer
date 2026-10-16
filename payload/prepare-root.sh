#!/usr/bin/env bash
# Executed as root against an extracted, disposable ISO filesystem.
set -Eeuo pipefail
root=$(realpath -e -- "$1")
work=$(realpath -e -- "$2")
payload=$(realpath -e -- "$3")
[[ $root == "$work/rootfs" && $root != / ]] || exit 1
[[ -f $root/usr/bin/pacman && -f $root/etc/mkinitcpio.conf.d/archiso.conf ]] || {
    echo 'Expected a CachyOS Archiso rootfs with its live initramfs configuration.' >&2; exit 1;
}
[[ -f $root/usr/local/bin/calamares-online.sh ]] || {
    echo 'Unsupported original ISO: missing CachyOS installer launcher.' >&2; exit 1;
}
[[ ! -e $root/var/tmp/bcachefs-remaster ]] || {
    echo 'Reserved build directory already exists in image.' >&2; exit 1;
}
mkdir -p "$root/var/tmp/bcachefs-remaster"
cp -a "$payload/." "$root/var/tmp/bcachefs-remaster/"
# Current host PUBLIC keyring files only; no private signing keys are copied.
mkdir -p "$root/usr/share/pacman/keyrings"
for name in archlinux cachyos; do
    for file in "$name.gpg" "$name-trusted" "$name-revoked"; do
        install -m 0644 "/usr/share/pacman/keyrings/$file" "$root/usr/share/pacman/keyrings/"
    done
done
# A private mount namespace also contains arch-chroot's temporary mounts.
# shellcheck disable=SC2016
unshare --mount --propagation private bash -c '
    mount --bind "$1" "$1"
    exec arch-chroot "$1" /usr/bin/bash /var/tmp/bcachefs-remaster/build-in-image.sh
' _ "$root"
mkdir -p "$work/installer-package"
cp "$root"/var/tmp/bcachefs-remaster/out/*.pkg.tar.* "$work/installer-package/"
cp "$root/var/tmp/bcachefs-remaster/build-report.txt" "$work/build-report.txt"
cp "$root/var/tmp/bcachefs-remaster/runtime-libraries.txt" "$work/runtime-libraries.txt"
while read -r kind base version; do
    [[ $kind == KERNEL ]] || continue
    [[ $base == linux-cachyos || $base == linux-cachyos-lts ]] || exit 1
    install -m 0644 "$root/usr/lib/modules/$version/vmlinuz" "$work/boot/vmlinuz-$base"
    install -m 0644 "$root/boot/initramfs-$base.img" "$work/boot/initramfs-$base.img"
done < "$work/build-report.txt"
cp "$root/var/tmp/bcachefs-remaster/pkglist.x86_64.txt" "$work/pkglist.x86_64.txt"
# Remove this build's sources and download cache, never the installed toolchain.
python3 - "$root" <<'PY'
from pathlib import Path
import shutil, sys
root = Path(sys.argv[1])
shutil.rmtree(root/'var/tmp/bcachefs-remaster')
cache = root/'var/cache/pacman/pkg'
for p in cache.iterdir():
    if p.is_file() or p.is_symlink(): p.unlink()
PY
