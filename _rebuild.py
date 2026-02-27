#!/usr/bin/env python3
"""Full Docker image rebuild on Pi.

Lastar opp ALLE filer (Dockerfile, Python, frontend, etc.) til Pi-repo
og startar docker build + docker compose up -d.

Krev: pip install paramiko
"""
import paramiko
import sys
import os
import time

PI_HOST = '192.168.1.160'
PI_USER = 'sverre'
PI_PASS = 'Svkme6199' + chr(33)
CONTAINER = 'pqtech-opendaq'
BASE = 'D:/Koding/dewesoft/OpenDackoConteiner'
PI_REPO = '/home/sverre/OpenDackoConteiner'

# Filer som Docker-buildet treng (matchar Dockerfile COPY + build context)
DOCKER_FILES = [
    'Dockerfile',
    'docker-compose.yml',
    'docker-entrypoint.sh',
    '99-dewesoft.rules',
    'dewesoft_stubs/platform_control.sh',
    # Python-filer (kopiert i Dockerfile)
    'opendaq_server.py',
    'web_ui.py',
    'usbip_manager.py',
    'sirius_usb_probe.py',
    'sirius_protokoll.py',
    'sirius_dekoder.py',
    'sirius_adc_leser.py',
    'sirius_sniffer.py',
    'sirius_protokoll_impl.py',
    'sirius_driver.py',
    'sirius_init_sekvens.py',
    'sirius_server.py',
    'opendaq_bro.py',
    'kanal_konfig.py',
    'mqtt_konfig.py',
    'mqtt_klient.py',
    'enhet_konfig.py',
]

print(f'=== Full rebuild: {PI_HOST} ===\n')

ssh = paramiko.SSHClient()
ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
ssh.connect(PI_HOST, username=PI_USER, password=PI_PASS, timeout=10)

sftp = ssh.open_sftp()

# Sikre at dewesoft_stubs-dir finst på Pi
try:
    sftp.mkdir(f'{PI_REPO}/dewesoft_stubs')
except IOError:
    pass  # finst allereie

# 1. Last opp alle filer
print('1. Lastar opp filer til Pi-repo...')
for f in DOCKER_FILES:
    local = f'{BASE}/{f}'
    remote = f'{PI_REPO}/{f}'
    if not os.path.exists(local):
        print(f'  ADVARSEL: {f} finst ikkje lokalt, hoppar over')
        continue
    sftp.put(local, remote)
    print(f'  {f}')

# Last opp frontend-filer (heile frontend/-mappa for npm build i Docker)
print('\n  Lastar opp frontend/...')
frontend_local = f'{BASE}/frontend'
frontend_remote = f'{PI_REPO}/frontend'

def upload_dir(local_dir, remote_dir):
    """Rekursivt last opp ei mappe, hopp over node_modules og dist."""
    try:
        sftp.mkdir(remote_dir)
    except IOError:
        pass
    for item in os.listdir(local_dir):
        if item in ('node_modules', 'dist', '.vite'):
            continue
        local_path = os.path.join(local_dir, item)
        remote_path = f'{remote_dir}/{item}'
        if os.path.isdir(local_path):
            upload_dir(local_path, remote_path)
        else:
            sftp.put(local_path, remote_path)

upload_dir(frontend_local, frontend_remote)
print('  frontend/ ferdig')

sftp.close()

# 2. Bygg Docker-image på Pi
print('\n2. Startar Docker build (kan ta 30-60 min)...')
print('   Bygget køyrer i bakgrunnen via nohup.\n')

# Bruk nohup + tee slik at bygget held fram sjølv om SSH avbryt
build_cmd = (
    f'cd {PI_REPO} && '
    f'nohup sudo docker build -t opendaq-sirius --build-arg PARALLELLE_JOBBER=2 . '
    f'> /tmp/docker-build.log 2>&1 & echo "BUILD_PID=$!"'
)
stdin, stdout, stderr = ssh.exec_command(build_cmd, timeout=30)
try:
    out = stdout.read(4096).decode(errors='replace')
    print(f'   {out.strip()}')
except Exception:
    pass
print('   Build starta. Logg: /tmp/docker-build.log')
print('   Sjekk status: ssh sverre@192.168.1.160 "tail -20 /tmp/docker-build.log"')
print('   Når ferdig: ssh sverre@192.168.1.160 "cd ~/OpenDackoConteiner && sudo docker compose up -d"')

ssh.close()
print('\nDone. Bygget køyrer i bakgrunnen på Pi.')
