#!/usr/bin/env python3
"""Deploy Fase 1+2+4 files to Pi and restart container.

Deployer Python-filer via docker cp (inga rebuild nødvendig).
Oppdaterer også docker-compose.yml på Pi for nye env vars (SAMPLE_RATE=20000, memory=1G).
"""
import paramiko
import sys

PI_HOST = '192.168.1.160'
PI_USER = 'sverre'
PI_PASS = 'Svkme6199!'
CONTAINER = 'pqtech-opendaq'
BASE = 'D:/Koding/dewesoft/OpenDackoConteiner'
PI_REPO = '/home/sverre/OpenDackoConteiner'

ssh = paramiko.SSHClient()
ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
ssh.connect(PI_HOST, username=PI_USER, password=PI_PASS, timeout=10)

sftp = ssh.open_sftp()

# Python-filer som skal inn i containeren via docker cp
container_files = [
    (f'{BASE}/opendaq_bro.py',        '/tmp/opendaq_bro.py',        '/app/opendaq_bro.py'),
    (f'{BASE}/sirius_driver.py',      '/tmp/sirius_driver.py',       '/app/sirius_driver.py'),
    (f'{BASE}/sirius_init_sekvens.py', '/tmp/sirius_init_sekvens.py', '/app/sirius_init_sekvens.py'),
    (f'{BASE}/kanal_konfig.py',       '/tmp/kanal_konfig.py',        '/app/kanal_konfig.py'),
    (f'{BASE}/sirius_server.py',      '/tmp/sirius_server.py',       '/app/sirius_server.py'),
    (f'{BASE}/sirius_protokoll_impl.py', '/tmp/sirius_protokoll_impl.py', '/app/sirius_protokoll_impl.py'),
    (f'{BASE}/web_ui.py',             '/tmp/web_ui.py',              '/app/web_ui.py'),
    (f'{BASE}/enhet_konfig.py',       '/tmp/enhet_konfig.py',        '/app/enhet_konfig.py'),
    (f'{BASE}/mqtt_konfig.py',        '/tmp/mqtt_konfig.py',         '/app/mqtt_konfig.py'),
    (f'{BASE}/mqtt_klient.py',        '/tmp/mqtt_klient.py',         '/app/mqtt_klient.py'),
    (f'{BASE}/usbip_manager.py',      '/tmp/usbip_manager.py',       '/app/usbip_manager.py'),
]

# docker-compose.yml oppdaterast på Pi-repo (for SAMPLE_RATE env + memory limit)
repo_files = [
    (f'{BASE}/docker-compose.yml', f'{PI_REPO}/docker-compose.yml'),
]

print(f'=== Deploy til {PI_HOST} ({CONTAINER}) ===\n')

# 1. Kopier Python-filer til container
for local, remote_tmp, container_path in container_files:
    filename = local.split('/')[-1]
    sftp.put(local, remote_tmp)
    print(f'  Uploaded {filename}')
    stdin, stdout, stderr = ssh.exec_command(
        f'sudo docker cp {remote_tmp} {CONTAINER}:{container_path}', timeout=15)
    stdout.read()
    err = stderr.read().decode(errors='replace').strip()
    if err:
        print(f'    FEIL: {err}')
    else:
        print(f'    -> {container_path}')

# 2. Oppdater docker-compose.yml på Pi-repo
for local, remote in repo_files:
    filename = local.split('/')[-1]
    sftp.put(local, remote)
    print(f'  Uploaded {filename} -> {remote}')

sftp.close()

# 3. Restart container (plukkar opp nye Python-filer)
print('\nRestartar container...')
stdin, stdout, stderr = ssh.exec_command(f'sudo docker restart {CONTAINER}', timeout=60)
out = stdout.read().decode(errors='replace').strip()
err = stderr.read().decode(errors='replace').strip()
print(f'  {out or err}')

# 4. Vent litt og sjekk loggar
import time
time.sleep(5)
print('\nSjekkar loggar (siste 30 linjer)...')
stdin, stdout, stderr = ssh.exec_command(
    f'sudo docker logs --tail 30 {CONTAINER} 2>&1', timeout=15)
logs = stdout.read().decode('utf-8', errors='replace')
print(logs.encode('ascii', errors='replace').decode('ascii'))

ssh.close()
print('Deploy ferdig!')
