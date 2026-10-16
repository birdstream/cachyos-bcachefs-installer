#!/usr/bin/env python3
"""Apply a version-checked source overlay; never touches disks.
SPDX-License-Identifier: GPL-3.0-or-later
"""
from pathlib import Path
import shutil
import sys

root = Path(sys.argv[1]).resolve()
kit = Path(__file__).resolve().parent

def edit(rel, before, after, count=1):
    p = root / rel
    s = p.read_text()
    if s.count(before) != count:
        raise SystemExit(f'Upstream changed: {rel}: expected {count} occurrence(s) of {before[:90]!r}')
    p.write_text(s.replace(before, after))

if (root / '.bcachefs-tiering-overlay').exists():
    raise SystemExit('Overlay already applied. Use a clean source tree.')
# Export a read-only snapshot of the native partition plan on every exit from
# the partition page. The native partition job exports it again after execution.
edit('src/modules/partition/jobs/FillGlobalStorageJob.h',
     '    QVariantList createPartitionList() const;',
     'public:\n    QVariantList createPartitionList() const;\nprivate:')
edit('src/modules/partition/jobs/FillGlobalStorageJob.cpp',
     '            lst << mapForPartition( *it, hash.value( ( *it )->partitionPath() ) );',
     '''            auto map = mapForPartition( *it, hash.value( ( *it )->partitionPath() ) ).toMap();
            map.insert( "disk", device->deviceNode() );
            map.insert( "gpt", device->partitionTable() && device->partitionTable()->type() == PartitionTable::gpt );
            map.insert( "firstSector", QString::number( ( *it )->firstSector() ) );
            map.insert( "lastSector", QString::number( ( *it )->lastSector() ) );
            lst << map;''')
edit('src/modules/partition/core/PartitionCoreModule.h',
     '    Calamares::JobList jobs( const Config* ) const;',
     '    Calamares::JobList jobs( const Config* ) const;\n    QVariantList bcachefsPartitionPlan() const;')
p = root/'src/modules/partition/core/PartitionCoreModule.h'
p.write_text('#include <QVariantList>\n' + p.read_text())
edit('src/modules/partition/core/PartitionCoreModule.cpp',
     'Calamares::JobList\nPartitionCoreModule::jobs( const Config* config ) const',
     '''QVariantList
PartitionCoreModule::bcachefsPartitionPlan() const
{
    QList< Device* > devices;
    for ( const auto* info : m_deviceInfos ) devices << info->device.data();
    FillGlobalStorageJob snapshot( nullptr, devices, QString() );
    return snapshot.createPartitionList();
}

Calamares::JobList
PartitionCoreModule::jobs( const Config* config ) const''')
edit('src/modules/partition/PartitionViewStep.cpp', '#include <QFormLayout>',
     '#include <QScopeGuard>\n#include <QFormLayout>')
edit('src/modules/partition/PartitionViewStep.cpp',
     'PartitionViewStep::onLeave()\n{',
     '''PartitionViewStep::onLeave()
{
    const auto bcachefsExport = qScopeGuard( [this] {
        Calamares::JobQueue::instance()->globalStorage()->insert(
            "bcachefsPartitionPlan", m_core->bcachefsPartitionPlan() );
    } );''')
edit('src/modules/partition/jobs/FillGlobalStorageJob.cpp',
     '#include <kpmcore/core/partition.h>',
     '#include <kpmcore/core/partition.h>\n#include <kpmcore/core/partitiontable.h>')
# Offer bcachefs only in the common and Limine lists. GRUB is unsupported here.
p = root / 'src/modules/partition/partition.conf'
s = p.read_text()
old = 'availableFileSystemTypes:  ["btrfs","xfs","ext4","f2fs","zfs"]'
assert s.count(old) == 3, 'Unexpected filesystem lists'
parts = s.split(old)
s = parts[0] + old[:-1] + ',"bcachefs"]' + parts[1] + old + parts[2] + old[:-1] + ',"bcachefs"]' + parts[3]
p.write_text(s)
# Use all explicitly formatted members for the initial mount; keep the original
# primary device in GS for other Calamares modules and preserve the root UUID.
edit('src/modules/mount/main.py', '\n    device = partition["device"]\n',
     '''
    device = partition["device"]
    if fstype == "bcachefs" and partition.get("mountPoint") == "/":
        members = libcalamares.globalstorage.value("bcachefsDevices")
        if members:
            device = ":".join(members)
''')
edit('src/modules/mount/main.py',
     '\n            libcalamares.utils.warning("Cannot mount {}".format(device))',
     '''
            libcalamares.utils.warning("Cannot mount {}".format(device))
            if fstype == "bcachefs":
                raise RuntimeError("Cannot mount bcachefs installation root")''')
# The target configuration relies on root=UUID and a udev mkinitcpio hook.
edit('src/modules/bootloader/main.py', '    return kernel_params',
     '''    if libcalamares.globalstorage.value("bcachefsInstalled"):
        kernel_params.append("rootfstype=bcachefs")
    return kernel_params''')
# The online installer may update its own package. Preserve this experimental
# build throughout a live session via pacman configuration in the ISO builder.
for module in ('bcachefstiering', 'bcachefsjob'):
    shutil.copytree(kit/'overlay'/module, root/'src/modules'/module)
for phase in ('preflight', 'format', 'configure', 'verify'):
    (root/'src/modules/bcachefsjob'/f'bcachefs-{phase}.conf').write_text(f'---\nphase: {phase}\n')
instances = ''.join(f'\n- id: {phase}\n  module: bcachefsjob\n  config: bcachefs-{phase}.conf\n' for phase in ('preflight','format','configure','verify'))
for name in ('settings_online.conf', 'settings.conf'):
    p = root/name
    s = p.read_text()
    if s.count('sequence:') != 1 or '  - initcpio\n' not in s:
        raise SystemExit('Unexpected settings sequence')
    s = s.replace('sequence:', instances + '\nsequence:')
    s = s.replace('  - partition\n', '  - partition\n  - bcachefstiering\n', 1)
    s = s.replace('- exec:\n  - partition\n', '- exec:\n  - bcachefsjob@preflight\n  - partition\n  - bcachefsjob@format\n')
    s = s.replace('  - initcpiocfg\n', '  - bcachefsjob@configure\n  - initcpiocfg\n')
    s = s.replace('  - initcpio\n', '  - initcpio\n  - bcachefsjob@verify\n')
    p.write_text(s)
(root/'.bcachefs-tiering-overlay').write_text('0.2.0-alpha1\n')
print('Applied bcachefs installer overlay 0.2.0-alpha1:', root)
