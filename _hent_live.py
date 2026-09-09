#!/usr/bin/env python3
"""Hent live-data frå Pi-containeren."""
import paramiko

ssh = paramiko.SSHClient()
ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
ssh.connect('192.168.1.160', username='sverre', password='Svkme6199!', timeout=10)

stdin, stdout, stderr = ssh.exec_command(
    "sudo docker exec opendaq-sirius python3 -c \""
    "import urllib.request, json; "
    "r = urllib.request.urlopen('http://localhost:8080/api/kanalar/live'); "
    "d = json.loads(r.read()); "
    "odaq = d.get('opendaq', {}); "
    "drv = d.get('driver', {}); "
    "[print(f'{k}: rms={odaq[k].get(chr(114)+chr(109)+chr(115),0):.2f}  "
    "snitt={odaq[k].get(chr(115)+chr(110)+chr(105)+chr(116)+chr(116),0):.2f}  "
    "topp={odaq[k].get(chr(116)+chr(111)+chr(112)+chr(112),0):.2f}') "
    "for k in sorted(odaq) if k.startswith('kanal')]; "
    "print(); "
    "[print(f'{k} raw: min={min(drv[k].get(chr(118)+chr(101)+chr(114)+chr(100)+chr(105)+chr(101)+chr(114),[0]))}"
    " max={max(drv[k].get(chr(118)+chr(101)+chr(114)+chr(100)+chr(105)+chr(101)+chr(114),[0]))}') "
    "for k in sorted(drv) if k.startswith('kanal')]"
    "\"",
    timeout=15)

result = stdout.read().decode('utf-8', errors='replace')
print(result)
ssh.close()
