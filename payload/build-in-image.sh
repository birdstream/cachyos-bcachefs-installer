#!/usr/bin/env bash
set -Eeuo pipefail
export LC_ALL=C
cd /var/tmp/bcachefs-remaster
fail() { printf 'BUILD ERROR: %s\n' "$*" >&2; exit 1; }
[[ -f /etc/mkinitcpio.conf.d/archiso.conf && -f package/PKGBUILD ]] || exit 1
cp -a /etc/pacman.conf /etc/pacman.conf.before-bcachefs-remaster
install -m 0644 generic-pacman.conf /etc/pacman.conf
# Version-independent, bcachefs-only BTF workaround is in place before DKMS hooks.
python3 configure-image.py
pacman-key --init
pacman-key --populate archlinux cachyos
# Update the keyrings first, then immediately perform a complete system upgrade.
pacman -Sy --needed --noconfirm archlinux-keyring cachyos-keyring
pacman -Syu --needed --noconfirm \
    base-devel git python binutils boost cmake doxygen extra-cmake-modules ninja \
    python-jsonschema python-pyaml python-unidecode qt6-tools \
    bcachefs-tools bcachefs-dkms dkms clang llvm lld rust rust-src rust-bindgen \
    linux-cachyos linux-cachyos-headers linux-cachyos-lts linux-cachyos-lts-headers \
    mkinitcpio mkinitcpio-archiso
id bcachefsbuild >/dev/null 2>&1 && fail 'Reserved build account already exists.'
useradd --system --create-home --home-dir /var/tmp/bcachefs-remaster/home --shell /bin/bash bcachefsbuild
chown -R bcachefsbuild:bcachefsbuild package
runuser -u bcachefsbuild -- bash -c \
    'cd /var/tmp/bcachefs-remaster/package; makepkg --printsrcinfo' > package-info.txt
mapfile -t build_deps < <(awk '$1 == "depends" || $1 == "makedepends" {print $3}' package-info.txt | sort -u)
(( ${#build_deps[@]} )) || fail 'Cannot determine Calamares build dependencies.'
pacman -S --needed --noconfirm "${build_deps[@]}"
runuser -u bcachefsbuild -- bash -c \
    'cd /var/tmp/bcachefs-remaster/package; makepkg --config /var/tmp/bcachefs-remaster/makepkg.conf --cleanbuild --noconfirm --log'
shopt -s nullglob
packages=(package/cachyos-calamares-next-*.pkg.tar.zst)
[[ ${#packages[@]} == 1 ]] || fail 'Expected one built Calamares package.'
mkdir out
cp "${packages[0]}" out/
pacman -U --noconfirm "${packages[0]}"
userdel bcachefsbuild
[[ -f /usr/lib/calamares/modules/bcachefsjob/core.py &&
   -f /usr/lib/calamares/modules/bcachefstiering/libcalamares_viewmodule_bcachefstiering.so ]] || fail 'Installer modules were not packaged.'
# Make the actual desktop launcher use the installed custom package.
install -m 0755 calamares-online.sh /usr/local/bin/calamares-online.sh
install -m 0644 /usr/share/calamares/settings_online.conf /etc/calamares/settings.conf
grep -q 'bcachefsjob@configure' /etc/calamares/settings.conf || fail 'Custom installer sequence missing.'
/usr/lib/ld-linux-x86-64.so.2 --list /usr/bin/calamares > runtime-libraries.txt
python3 verify-isa.py
configs=(/usr/src/bcachefs-*/dkms.conf)
[[ ${#configs[@]} == 1 ]] || fail 'Expected one bcachefs DKMS source tree.'
dkms_id=$(bash -c 'source "$1"; printf "%s/%s" "$PACKAGE_NAME" "$PACKAGE_VERSION"' _ "${configs[0]}")
[[ $dkms_id == bcachefs/* && $dkms_id != bcachefs/ ]] || fail 'Invalid DKMS package identity.'
upstream_version() { local v=${1#*:}; printf '%s' "${v%-*}"; }
read -r _ tools_ver < <(pacman -Q bcachefs-tools)
read -r _ dkms_ver < <(pacman -Q bcachefs-dkms)
[[ $(upstream_version "$tools_ver") == "$(upstream_version "$dkms_ver")" ]] || fail 'bcachefs-tools and DKMS releases differ.'
printf 'PACKAGE bcachefs-tools %s\nPACKAGE bcachefs-dkms %s\n' "$tools_ver" "$dkms_ver" > build-report.txt
for base in linux-cachyos linux-cachyos-lts; do
    kernels=()
    for p in /usr/lib/modules/*/pkgbase; do
        [[ $(<"$p") == "$base" ]] && kernels+=("${p%/pkgbase}")
    done
    [[ ${#kernels[@]} == 1 ]] || fail "Expected one installed kernel for $base."
    k=${kernels[0]##*/}
    [[ -f /usr/lib/modules/$k/build/Makefile && -f /usr/lib/modules/$k/vmlinuz ]] || fail "Missing kernel/headers: $k"
    dkms install "$dkms_id" -k "$k"
    depmod -a "$k"
    module=$(modinfo -k "$k" -n bcachefs)
    [[ $module == */updates/dkms/bcachefs.ko* ]] || fail "No DKMS module for $k"
    vermagic=$(modinfo -k "$k" -F vermagic bcachefs)
    [[ ${vermagic%% *} == "$k" ]] || fail "Module vermagic mismatch: $k"
    modprobe --set-version "$k" --show-depends bcachefs
    mkinitcpio -k "$k" -c /etc/mkinitcpio.conf.d/archiso.conf -g "/boot/initramfs-$base.img"
    lsinitcpio "/boot/initramfs-$base.img" > "$base-initramfs.txt"
    grep -F "usr/lib/modules/$k/" "$base-initramfs.txt" | grep -Eq '/bcachefs\.ko(\.(zst|xz|gz))?$' || fail "bcachefs missing from $base initramfs."
    printf 'KERNEL %s %s\n' "$base" "$k" >> build-report.txt
done
install -m 0644 build-report.txt /etc/bcachefs-live-build.txt
pacman -Q > pkglist.x86_64.txt
printf '\nCalamares and both kernel modules built and verified.\n'
