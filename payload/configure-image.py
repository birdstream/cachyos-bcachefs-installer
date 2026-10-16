from pathlib import Path
def _install_bcachefs_btf_workaround(root):
    # Install before pacman can invoke its DKMS hooks. Keep it for later updates.
    content = '# BEGIN bcachefs-installer BTF workaround v1\n# Missing bcachefs.ko.BTF.1: omit BTF only for bcachefs on linux-cachyos.\n# Remove this marked block after the upstream BTF build issue is fixed.\nif [[ -r /usr/lib/modules/${kernelver}/pkgbase ]] &&\n   [[ $(< "/usr/lib/modules/${kernelver}/pkgbase") == linux-cachyos ]]; then\n    _bc_btf_make=\n    if [[ -n ${MAKE[0]:-} && -z ${MAKE_MATCH[0]:-} ]]; then\n        _bc_btf_make=${MAKE[0]}\n    fi\n    for _bc_btf_i in "${!MAKE[@]}"; do\n        if [[ -n ${MAKE[$_bc_btf_i]} && -n ${MAKE_MATCH[$_bc_btf_i]:-} &&\n              $kernelver =~ ${MAKE_MATCH[$_bc_btf_i]} ]]; then\n            _bc_btf_make=${MAKE[$_bc_btf_i]}\n        fi\n    done\n    if [[ -z $_bc_btf_make ]]; then\n        _bc_btf_make="make -C ${kernel_source_dir:-/usr/lib/modules/${kernelver}/build} M=${dkms_tree:-/var/lib/dkms}/bcachefs/${PACKAGE_VERSION}/build"\n    fi\n    if [[ $_bc_btf_make != *" CONFIG_DEBUG_INFO_BTF_MODULES=" ]]; then\n        _bc_btf_make+=" CONFIG_DEBUG_INFO_BTF_MODULES="\n    fi\n    MAKE[0]="$_bc_btf_make"\n    MAKE_MATCH[0]=\'\'\n    # DKMS safe_source copies populated indices back; it does not propagate\n    # array unsets. Keep every alternative command protected as well.\n    for _bc_btf_i in "${!MAKE[@]}"; do\n        if [[ ${MAKE[$_bc_btf_i]} != *" CONFIG_DEBUG_INFO_BTF_MODULES=" ]]; then\n            MAKE[$_bc_btf_i]+=" CONFIG_DEBUG_INFO_BTF_MODULES="\n        fi\n    done\n    unset _bc_btf_make _bc_btf_i\nfi\n# END bcachefs-installer BTF workaround v1\n'
    root = Path(root).resolve()
    directory = root / 'etc/dkms'
    if not directory.resolve().is_relative_to(root):
        raise RuntimeError('DKMS configuration directory escapes target root.')
    directory.mkdir(parents=True, exist_ok=True)
    paths = [directory / 'bcachefs.conf', *sorted(directory.glob('bcachefs-*.conf'))]
    changes = []
    for path in paths:
        if not path.resolve().is_relative_to(root) or (path.exists() and not path.is_file()):
            raise RuntimeError('Unsafe bcachefs DKMS configuration: ' + str(path))
        old = path.read_text() if path.exists() else ''
        if content in old:
            continue
        if '# BEGIN bcachefs-installer BTF workaround' in old:
            raise RuntimeError('Unrecognized existing BTF workaround: ' + str(path))
        changes.append((path, old.rstrip() + '\n\n' + content))
    for path, content in changes:
        path.write_text(content)
        path.chmod(0o644)


_install_bcachefs_btf_workaround(Path('/'))
p = Path('/etc/mkinitcpio.conf.d/archiso.conf')
s = p.read_text()
if 'MODULES+=(bcachefs)' not in s:
    p.write_text(s.rstrip() + '\nMODULES+=(bcachefs)\n')
p = Path('/etc/modules-load.d/bcachefs.conf')
p.parent.mkdir(parents=True, exist_ok=True)
p.write_text('bcachefs\n')
