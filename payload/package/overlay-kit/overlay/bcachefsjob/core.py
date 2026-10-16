"""Validated bcachefs installation plans. Preview and tests never write disks.
SPDX-License-Identifier: GPL-3.0-or-later
"""
import json
import os
import re
import subprocess
import sys
import uuid

class PlanError(ValueError):
    pass

def run(argv, **kwargs):
    return subprocess.run(argv, check=True, text=True, stdout=subprocess.PIPE,
                          stderr=subprocess.PIPE, **kwargs).stdout.strip()

def key(p):
    return (p.get('disk'), str(p.get('firstSector')), str(p.get('lastSector')))

def validate(plan, options, bootloader, efi):
    roots = [p for p in plan if p.get('mountPoint') == '/']
    if len(roots) != 1:
        raise PlanError('Exakt en rotpartition / krävs.')
    root = roots[0]
    if root.get('fs') != 'bcachefs':
        return {'enabled': False}
    if not efi or bootloader != 'limine':
        raise PlanError('Den här versionen kräver UEFI och Limine för bcachefs-root.')
    boots = [p for p in plan if p.get('mountPoint') == '/boot']
    if len(boots) != 1 or boots[0].get('fs') not in ('fat32', 'vfat'):
        raise PlanError('Skapa en separat FAT32 EFI-partition på /boot, rekommenderat 4 GiB.')
    if boots[0].get('luksMapperName'):
        raise PlanError('/boot får inte vara krypterad.')
    members = options.get('members', [])
    if not members or len(members) > 16:
        raise PlanError('Välj mellan 1 och 16 bcachefs-partitioner.')
    known = {key(p): p for p in plan}
    if len(known) != len(plan):
        raise PlanError('Partitionsplanen har tvetydiga identiteter.')
    normalized = []
    disks = set()
    for m in members:
        p = known.get(key(m))
        if p is None or p.get('fs') != 'bcachefs' or p.get('claimed') is not True:
            raise PlanError('Alla medlemmar måste vara valda för formatering som bcachefs.')
        if p.get('luksMapperName') or p.get('luksUuid'):
            raise PlanError('Krypterade bcachefs-medlemmar stöds inte i den här versionen.')
        if p.get('mountPoint') not in ('', None, '/'):
            raise PlanError('Extra medlemmar ska sakna monteringspunkt; endast root har /.')
        if p.get('gpt') is not True:
            raise PlanError('Alla bcachefs-medlemmar måste ligga på GPT-diskar.')
        disk = p.get('disk', '')
        if not re.fullmatch(r'/dev/[A-Za-z0-9_.-]+', disk):
            raise PlanError('Endast partitioner på vanliga fysiska diskar stöds.')
        if disk in disks:
            raise PlanError('Välj högst en bcachefs-medlem per fysisk disk.')
        disks.add(disk)
        if int(p['firstSector']) < 1 or int(p['lastSector']) < int(p['firstSector']):
            raise PlanError('Ogiltig partitionsgeometri.')
        role = m.get('role')
        if role not in ('ssd', 'hdd', 'normal'):
            raise PlanError('Ogiltig diskroll.')
        normalized.append({**p, 'role': role})
    if key(root) not in {key(p) for p in normalized}:
        raise PlanError('Rotpartitionen måste ingå i bcachefs-gruppen.')
    # Keep root first; this also gives a stable label per disk in the summary.
    normalized.sort(key=lambda p: p.get('mountPoint') != '/')
    compression = options.get('compression', 'zstd')
    if compression not in ('none', 'lz4', 'zstd'):
        raise PlanError('Komprimering måste vara none, lz4 eller zstd.')
    result = {'enabled': True, 'members': normalized, 'compression': compression}
    for name in ('data_replicas', 'metadata_replicas'):
        count = options.get(name, 1)
        if type(count) is not int or not 1 <= count <= min(3, len(disks)):
            raise PlanError('Antalet kopior måste rymmas på skilda fysiska diskar (1–3).')
        result[name] = count
    roles = {p['role'] for p in normalized}
    for name in ('foreground_target', 'background_target', 'promote_target', 'metadata_target'):
        target = options.get(name, '')
        if target not in ('', 'ssd', 'hdd', 'normal') or (target and target not in roles):
            raise PlanError('Varje valt tieringmål måste motsvara en diskroll i gruppen.')
        result[name] = target
    for i, p in enumerate(normalized):
        p['label'] = f"{p['role']}.disk{i}"
    return result

def disk_identity(path):
    data = json.loads(run(['lsblk', '--json', '--bytes', '--nodeps', '--output',
                          'PATH,TYPE,SIZE,MAJ:MIN,MODEL,SERIAL,WWN,RO,RM,LOG-SEC', path]))
    nodes = data.get('blockdevices', [])
    if len(nodes) != 1 or nodes[0].get('type') != 'disk' or nodes[0].get('ro'):
        raise PlanError(f'{path} är inte en skrivbar fysisk disk.')
    d = nodes[0]
    # Restrict first release to fixed disks. In particular, do not target the USB ISO.
    if d.get('rm'):
        raise PlanError('Flyttbara diskar kan inte användas som installationsmål här.')
    # Boost.Python GlobalStorage converts Python int to a signed C++ int.
    # Disk byte counts exceed that range; retain every digit as a string.
    return {k: str(d.get(k)) if k == 'size' else d.get(k) for k in ('path', 'size', 'maj:min', 'model', 'serial', 'wwn', 'log-sec')}

def fingerprint(result):
    return {p['disk']: disk_identity(p['disk']) for p in result['members']}

def assert_fingerprints(expected):
    if not expected:
        raise PlanError('Diskidentiteter saknas. Gå tillbaka till bcachefs-steget.')
    for path, identity in expected.items():
        if disk_identity(path) != identity:
            raise PlanError(f'Diskidentiteten har ändrats: {path}. Starta om installationen.')

def format_command(result, devices, fs_uuid):
    fs_uuid = str(uuid.UUID(fs_uuid))
    if len(devices) != len(result['members']) or len(set(devices)) != len(devices):
        raise PlanError('Antalet unika enheter stämmer inte med planen.')
    if any(not re.fullmatch(r'/dev/[A-Za-z0-9_.-]+', x) for x in devices):
        raise PlanError('Ogiltig partitionssökväg.')
    cmd = ['bcachefs', 'format', '--force', f'--uuid={fs_uuid}',
           f"--compression={result['compression']}",
           f"--data_replicas={result['data_replicas']}",
           f"--metadata_replicas={result['metadata_replicas']}"]
    for name in ('foreground_target', 'background_target', 'promote_target', 'metadata_target'):
        if result[name]:
            cmd.append(f'--{name}={result[name]}')
    for p, device in zip(result['members'], devices):
        cmd.extend([f"--label={p['label']}", device])
    return cmd

def resolve_members(result, actual):
    resolved = []
    for planned in result['members']:
        matches = [p for p in actual if key(p) == key(planned)]
        if len(matches) != 1:
            raise PlanError('Partitionsplanen stämmer inte längre med utförd partitionering.')
        p = matches[0]
        if (p.get('fs') != 'bcachefs' or p.get('claimed') is not True
                or p.get('luksMapperName') or p.get('mountPoint') != planned.get('mountPoint')):
            raise PlanError('En medlem har inte formaterats enligt den godkända planen.')
        if not p.get('uuid') or not p.get('device'):
            raise PlanError('Nyformaterad medlem saknar UUID eller enhetsnamn.')
        resolved.append(p)
    return resolved

def assert_partition(p, expected_identity):
    path = p['device']
    st = os.stat(path)
    import stat
    if not stat.S_ISBLK(st.st_mode):
        raise PlanError('Målet är inte en blockenhet.')
    d = json.loads(run(['lsblk', '--json', '--bytes', '--nodeps', '--output',
                       'PATH,TYPE,PKNAME,START,SIZE,LOG-SEC,MOUNTPOINTS', path]))['blockdevices'][0]
    if d['type'] != 'part' or any(d.get('mountpoints') or []):
        raise PlanError('Målet måste vara en omonterad partition.')
    if os.path.realpath('/dev/' + os.path.basename(d['pkname'])) != os.path.realpath(p['disk']):
        raise PlanError('Partitionen ligger på fel disk.')
    # KPMcore uses device logical sectors; Linux sysfs start/size use 512-byte sectors.
    name = os.path.basename(os.path.realpath(path))
    logical = int(expected_identity['log-sec'])
    from pathlib import Path
    start_bytes = int(Path(f'/sys/class/block/{name}/start').read_text()) * 512
    size_bytes = int(Path(f'/sys/class/block/{name}/size').read_text()) * 512
    if (start_bytes != int(p['firstSector']) * logical or
            size_bytes != (int(p['lastSector']) - int(p['firstSector']) + 1) * logical):
        raise PlanError('Partitionens faktiska geometri har ändrats.')
    if list(Path(f'/sys/class/block/{name}/holders').iterdir()):
        raise PlanError('Partitionen används av device-mapper/RAID.')
    # Bypass blkid cache: require the freshly formatted native Calamares filesystem.
    if run(['blkid', '-p', '-s', 'TYPE', '-o', 'value', path]) != 'bcachefs':
        raise PlanError('Målet är inte den förväntade nyformaterade bcachefs-partitionen.')
    if run(['blkid', '-p', '-s', 'UUID', '-o', 'value', path]).lower() != p['uuid'].lower():
        raise PlanError('Medlemmens UUID har ändrats efter partitioneringen.')

def preview(payload):
    r = validate(payload['plan'], payload['options'], payload.get('bootloader'), payload.get('efi'))
    if r['enabled']:
        r['identities'] = fingerprint(r)
    return r

if __name__ == '__main__':
    try:
        print(json.dumps({'ok': True, 'result': preview(json.load(sys.stdin))}))
    except (PlanError, ValueError, KeyError, OSError, subprocess.SubprocessError) as e:
        print(json.dumps({'ok': False, 'error': str(e)}))
        sys.exit(1)
