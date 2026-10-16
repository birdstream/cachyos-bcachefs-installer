# SPDX-License-Identifier: GPL-3.0-or-later
import copy
import importlib.util
import json
import os
from pathlib import Path
import re
import subprocess
import traceback
import uuid
import libcalamares

# Calamares' Boost.Python exec_file does not supply __file__.
_module_dir = Path(libcalamares.job.working_path)
_spec = importlib.util.spec_from_file_location('bcachefs_installer_core', _module_dir / 'core.py')
core = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(core)

def pretty_name():
    return 'Bcachefs: validera diskar, tiering och uppstart'

def target_root():
    p = Path(libcalamares.globalstorage.value('rootMountPoint') or '').resolve()
    if str(p) in ('/', '.') or not p.is_dir():
        raise core.PlanError('Ogiltig målrot.')
    if core.run(['findmnt', '-n', '-o', 'FSTYPE', '--mountpoint', str(p)]) != 'bcachefs':
        raise core.PlanError('Målroten är inte monterad som bcachefs.')
    return p

def no_active_disk(path):
    tree = json.loads(core.run(['lsblk', '--json', '--output', 'PATH,MOUNTPOINTS', path]))
    def walk(nodes):
        for d in nodes:
            if any(d.get('mountpoints') or []):
                raise core.PlanError(f"Disken används: {d['path']}. Avmontera den innan installationen.")
            walk(d.get('children', []))
    walk(tree['blockdevices'])

def preflight(options):
    gs = libcalamares.globalstorage
    result = core.validate(gs.value('bcachefsPartitionPlan'), options,
                           gs.value('packagechooser_bootloader'), Path('/sys/firmware/efi').exists())
    if not result['enabled']:
        return
    if options.get('acknowledged') is not True:
        raise core.PlanError('Bcachefs-planen har inte godkänts.')
    core.assert_fingerprints(options.get('identities'))
    for disk in options['identities']:
        no_active_disk(disk)
    core.run(['modprobe', 'bcachefs'])
    # Remember exactly what passed BEFORE native partition jobs change disks.
    gs.insert('bcachefsApproved', {**result, 'identities': options['identities']})

def format_members():
    gs = libcalamares.globalstorage
    approved = gs.value('bcachefsApproved')
    if not approved or not approved.get('enabled'):
        raise core.PlanError('Förkontroll saknas; formatering blockeras.')
    core.assert_fingerprints(approved['identities'])
    partitions = copy.deepcopy(gs.value('partitions'))
    members = core.resolve_members(approved, partitions)
    for p in members:
        core.assert_partition(p, approved['identities'][p['disk']])
    fs_uuid = str(uuid.UUID(members[0]['uuid']))
    devices = [p['device'] for p in members]
    command = core.format_command(approved, devices, fs_uuid)
    libcalamares.utils.debug('Bcachefs format: ' + ' '.join(command))
    # This is the ONLY extra formatting operation. All candidates have just been
    # validated as the freshly formatted native Calamares partitions.
    core.run(command, input='')
    core.run(['udevadm', 'settle'])
    partuuids = []
    for device in devices:
        if core.run(['blkid', '-p', '-s', 'UUID', '-o', 'value', device]).lower() != fs_uuid:
            raise core.PlanError('UUID-kontrollen efter bcachefs-format misslyckades.')
        partuuids.append(str(uuid.UUID(core.run(['blkid', '-s', 'PARTUUID', '-o', 'value', device]))))
    if len(set(partuuids)) != len(partuuids):
        raise core.PlanError('Medlemmarna har inte unika GPT PARTUUID.')
    member_keys = {core.key(p) for p in members}
    result_partitions = []
    for p in partitions:
        if core.key(p) in member_keys:
            if p.get('mountPoint') != '/':
                continue  # Pool members must not become independent fstab entries.
            p['uuid'] = fs_uuid
        result_partitions.append(p)
    gs.insert('partitions', result_partitions)
    gs.insert('bcachefsDevices', devices)
    gs.insert('bcachefsInstalled', {**approved, 'uuid': fs_uuid, 'partuuids': partuuids, 'devices': devices})

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

def configure():
    gs = libcalamares.globalstorage
    config = gs.value('bcachefsInstalled')
    if not config:
        raise core.PlanError('Den skapade bcachefs-konfigurationen saknas.')
    root = target_root()
    kernel_bases = []
    for p in (root / 'usr/lib/modules').glob('*/pkgbase'):
        name = p.read_text().strip()
        if not re.fullmatch(r'linux[-a-zA-Z0-9_+.]*', name):
            raise core.PlanError('Ett okänt kärnpaket kan inte få headers automatiskt.')
        kernel_bases.append(name)
    if not kernel_bases:
        raise core.PlanError('Ingen installerad kärna hittades.')
    packages = ['bcachefs-tools', 'bcachefs-dkms', 'base-devel', 'clang', 'llvm', 'lld', 'rust', 'rust-src', 'rust-bindgen']
    for name in sorted(set(kernel_bases)):
        packages.extend([name, name + '-headers'])
    def target(args):
        return core.run(['arch-chroot', str(root), *args])
    _install_bcachefs_btf_workaround(root)
    target(['pacman', '-S', '--needed', '--noconfirm', *packages])
    versions = [target(['pacman', '-Q', name]).split()[1].split(':')[-1].rsplit('-', 1)[0]
                for name in ('bcachefs-tools', 'bcachefs-dkms')]
    if versions[0] != versions[1]:
        raise core.PlanError('Målsystemets bcachefs-tools och bcachefs-dkms har olika versioner.')
    configs = list((root / 'usr/src').glob('bcachefs-*/dkms.conf'))
    if len(configs) != 1:
        raise core.PlanError('Förväntade exakt ett bcachefs DKMS-källträd i målroten.')
    dkms_id = target(['bash', '-c', 'source "$1"; printf "%s/%s" "$PACKAGE_NAME" "$PACKAGE_VERSION"',
                      '_', '/' + str(configs[0].relative_to(root))])
    for p in (root / 'usr/lib/modules').glob('*/pkgbase'):
        k = p.parent.name
        target(['dkms', 'install', dkms_id, '-k', k])
        target(['depmod', '-a', k])
        if target(['modinfo', '-k', k, '-F', 'vermagic', 'bcachefs']).split()[0] != k:
            raise core.PlanError('Bcachefs-modulen matchar inte målkärnan.')
    files = {
        'etc/bcachefs-root-members': config['uuid'] + '\n' + '\n'.join(config['partuuids']) + '\n',
        'etc/mkinitcpio.conf.d/zz-bcachefs-tiering.conf':
            '# Generated by the bcachefs Calamares extension.\n'
            'MODULES+=(bcachefs)\n'
            '# No generic boot fsck on a multi-device bcachefs root.\n'
            '_bc_hooks=()\nfor _bc_hook in "${HOOKS[@]}"; do\n'
            '  [[ $_bc_hook == fsck ]] || _bc_hooks+=("$_bc_hook")\ndone\n'
            'HOOKS=("${_bc_hooks[@]}" bcachefs-tiering)\n'
            'unset _bc_hooks _bc_hook\n',
        'etc/modules-load.d/bcachefs.conf': 'bcachefs\n',
        'etc/bcachefs-install.json': json.dumps(config, indent=2) + '\n',
    }
    for name, content in files.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)
        path.chmod(0o644)
    for folder in ('install', 'hooks'):
        path = root / f'etc/initcpio/{folder}/bcachefs-tiering'
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text((_module_dir / ('initcpio-' + folder)).read_text())
        path.chmod(0o644)
    fstab = root / 'etc/fstab'
    lines = fstab.read_text().splitlines()
    found = 0
    for i, line in enumerate(lines):
        fields = line.split()
        if not line.startswith('#') and len(fields) >= 6 and fields[1] == '/':
            if fields[2] != 'bcachefs':
                raise core.PlanError('fstab anger fel rotfilsystem.')
            fields[0] = 'UUID=' + config['uuid']
            fields[4:] = ['0', '0']
            lines[i] = '\t'.join(fields)
            found += 1
    if found != 1:
        raise core.PlanError('Exakt en bcachefs-rootpost krävs i fstab.')
    fstab.write_text('\n'.join(lines) + '\n')

def verify():
    root = target_root()
    for p in (root / 'usr/lib/modules').glob('*/pkgbase'):
        name = p.read_text().strip()
        image = root / 'boot' / f'initramfs-{name}.img'
        listing = core.run(['lsinitcpio', str(image)])
        if not re.search(r'/bcachefs\.ko(?:\.(?:zst|xz|gz))?$', listing, re.M):
            raise core.PlanError(f'Bcachefs saknas i {image.name}.')
        if 'etc/bcachefs-root-members' not in listing or 'hooks/bcachefs-tiering' not in listing:
            raise core.PlanError('Initramfs saknar medlemslista eller bcachefs-uppstartshook.')

def run():
    gs = libcalamares.globalstorage
    options = gs.value('bcachefsOptions') or {}
    phase = libcalamares.job.configuration.get('phase')
    try:
        if phase == 'preflight':
            preflight(options)
        elif options.get('enabled'):
            {'format': format_members, 'configure': configure, 'verify': verify}[phase]()
    except Exception as e:
        libcalamares.utils.debug(traceback.format_exc())
        detail = str(e)
        if isinstance(e, subprocess.CalledProcessError):
            detail += '\n' + (e.stderr or '') + '\n' + (e.stdout or '')
        return ('Bcachefs-installationen stoppades', detail)
    return None
