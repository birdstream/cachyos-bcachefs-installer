from pathlib import Path
import os, re, subprocess

paths = {Path('/usr/bin/calamares')}
paths.update(Path('/usr/lib/calamares').rglob('*.so*'))
for line in Path('runtime-libraries.txt').read_text().splitlines():
    paths.update(Path(p) for p in re.findall(r'(/[^\s()]+)', line))
checked = 0
for p in sorted(paths):
    if not p.is_file():
        continue
    with p.open('rb') as f:
        if f.read(4) != b'\x7fELF': continue
    notes = subprocess.check_output(['readelf', '-n', str(p)], text=True)
    for line in notes.splitlines():
        if 'ISA needed:' in line and re.search(r'x86-64-v[234]', line):
            raise SystemExit(f'Non-baseline ISA requirement: {p}: {line}')
    checked += 1
if not checked: raise SystemExit('No installer ELF files checked.')
print(f'Checked {checked} installer/runtime ELF ISA notes (not instruction-level proof).')
