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
    (f'{BASE}/hub_konfig.py',         '/tmp/hub_konfig.py',          '/app/hub_konfig.py'),
    (f'{BASE}/hub_server.py',         '/tmp/hub_server.py',          '/app/hub_server.py'),
    (f'{BASE}/usbip_manager.py',      '/tmp/usbip_manager.py',       '/app/usbip_manager.py'),
    (f'{BASE}/oppdatering.py',        '/tmp/oppdatering.py',         '/app/oppdatering.py'),
    (f'{BASE}/brukar_auth.py',        '/tmp/brukar_auth.py',         '/app/brukar_auth.py'),
]

# docker-compose.yml oppdaterast på Pi-repo (for SAMPLE_RATE env + memory limit)
repo_files = [
    (f'{BASE}/docker-compose.yml', f'{PI_REPO}/docker-compose.yml'),
    (f'{BASE}/docker-entrypoint.sh', f'{PI_REPO}/docker-entrypoint.sh'),
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

import subprocess, os, time

# 3. Recreate container via docker-compose for å plukke opp nye env vars.
#    docker restart les IKKJE docker-compose.yml — berre docker-compose down/up gjer det.
print('\nRecreating container via docker-compose (plukkar opp env-endringar)...')
stdin, stdout, stderr = ssh.exec_command(
    f'cd {PI_REPO} && sudo docker compose down', timeout=60)
out = stdout.read().decode(errors='replace').strip()
err = stderr.read().decode(errors='replace').strip()
print(f'  down: {out or err or "OK"}')

stdin, stdout, stderr = ssh.exec_command(
    f'cd {PI_REPO} && sudo docker compose up -d', timeout=60)
out = stdout.read().decode(errors='replace').strip()
err = stderr.read().decode(errors='replace').strip()
print(f'  up: {out or err or "OK"}')

# Vent på at containeren er oppe
time.sleep(3)

# 4. Kopier Python-filer inn i den nye containeren (docker cp)
print('\nKopierer Python-filer til ny container...')
sftp2 = ssh.open_sftp()
for local, remote_tmp, container_path in container_files:
    filename = local.split('/')[-1]
    sftp2.put(local, remote_tmp)
    stdin, stdout, stderr = ssh.exec_command(
        f'sudo docker cp {remote_tmp} {CONTAINER}:{container_path}', timeout=15)
    stdout.read()
    err = stderr.read().decode(errors='replace').strip()
    if err:
        print(f'  {filename}: FEIL: {err}')
    else:
        print(f'  {filename} -> {container_path}')
sftp2.close()

# 5. Kopier frontend/dist/ til ny container via tar
print('\nKopierer frontend/dist/ til container...')
tar_path = '/tmp/frontend_dist.tar.gz'
subprocess.run(
    ['tar', 'czf', tar_path, '-C', f'{BASE}/frontend', 'dist'],
    check=True,
)
sftp3 = ssh.open_sftp()
sftp3.put(tar_path, '/tmp/frontend_dist.tar.gz')
sftp3.close()
os.remove(tar_path)
stdin, stdout, stderr = ssh.exec_command(
    f'sudo docker exec {CONTAINER} mkdir -p /app/frontend && '
    f'sudo docker cp /tmp/frontend_dist.tar.gz {CONTAINER}:/tmp/frontend_dist.tar.gz && '
    f'sudo docker exec {CONTAINER} tar xzf /tmp/frontend_dist.tar.gz -C /app/frontend',
    timeout=30)
stdout.read()
err = stderr.read().decode(errors='replace').strip()
if err:
    print(f'  FEIL: {err}')
else:
    print(f'  frontend/dist/ -> /app/frontend/dist/')

# 6. Restart for å plukke opp dei nye Python-filene
print('\nRestartar container (nye Python-filer)...')
stdin, stdout, stderr = ssh.exec_command(f'sudo docker restart {CONTAINER}', timeout=60)
out = stdout.read().decode(errors='replace').strip()
err = stderr.read().decode(errors='replace').strip()
print(f'  {out or err}')

# 6. Vent litt og sjekk loggar
time.sleep(5)
print('\nSjekkar loggar (siste 30 linjer)...')
stdin, stdout, stderr = ssh.exec_command(
    f'sudo docker logs --tail 30 {CONTAINER} 2>&1', timeout=15)
logs = stdout.read().decode('utf-8', errors='replace')
print(logs.encode('ascii', errors='replace').decode('ascii'))

ssh.close()
print('Deploy ferdig!')
