#!/usr/bin/env python3
"""Check MQTT and openDAQ status from inside container."""
import paramiko, sys, json

sys.stdout.reconfigure(encoding='utf-8', errors='replace')
ssh = paramiko.SSHClient()
ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
ssh.connect('192.168.1.160', username='sverre', password='Svkme6199!', timeout=10)

py_script = r"""
import urllib.request, json
urls = ['/api/mqtt/status', '/api/opendaq/verdiar', '/api/opendaq/status']
for url in urls:
    try:
        r = urllib.request.urlopen('http://localhost:8080' + url, timeout=5)
        data = r.read().decode('utf-8')
        print('===', url, '===')
        try:
            print(json.dumps(json.loads(data), indent=2, ensure_ascii=False))
        except:
            print(data[:500])
    except Exception as e:
        print('===', url, '=== ERROR:', e)
"""

# Write script to container and run it
sftp = ssh.open_sftp()
sftp.open('/tmp/_check.py', 'w').write(py_script)
sftp.close()

stdin, stdout, stderr = ssh.exec_command(
    'sudo docker cp /tmp/_check.py pqtech-opendaq:/tmp/_check.py && '
    'sudo docker exec pqtech-opendaq python3 /tmp/_check.py',
    timeout=15
)
print(stdout.read().decode('utf-8', errors='replace'))
err = stderr.read().decode('utf-8', errors='replace')
if err:
    print('STDERR:', err[:500])
ssh.close()
