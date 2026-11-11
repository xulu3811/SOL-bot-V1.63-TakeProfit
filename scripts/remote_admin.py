"""Authorized host runner. Credentials are consumed only for SSH authentication."""
from pathlib import Path
import argparse
import base64
import hashlib
import re
import socket
import sys
import paramiko

def connect():
    host = '43.163.192.120'
    transport = paramiko.Transport(socket.create_connection((host, 22), timeout=15))
    transport.start_client(timeout=20)
    key = transport.get_remote_server_key()
    fingerprint = base64.b64encode(hashlib.sha256(key.asbytes()).digest()).decode().rstrip('=')
    if key.get_name() != 'ssh-ed25519' or fingerprint != 'kcxK2s7I02eytqSahaEcFaTLe+mafXgvlYwvNMtWr9o':
        transport.close()
        raise RuntimeError('Host identity mismatch')
    rows = [line for line in Path(r'D:\SOL-Bot\FCM-Serv\serv-Acc.txt').read_text(encoding='utf-8-sig').splitlines() if line.strip()]
    if len(rows) == 3 and rows[0].strip() == host:
        rows = [re.split(r'[:=\uff1a]', rows[1], maxsplit=1)[-1].strip(), host, re.split(r'[:=\uff1a]', rows[2], maxsplit=1)[-1].strip()]
    if len(rows) != 3 or rows[0] != 'ubuntu' or rows[1] != host:
        raise RuntimeError('Unsupported account format')
    transport.auth_password(rows[0], rows[2], fallback=False)
    return transport

def main():
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    sys.stderr.reconfigure(encoding='utf-8', errors='replace')
    parser = argparse.ArgumentParser()
    parser.add_argument('script')
    parser.add_argument('--upload')
    parser.add_argument('--evidence')
    args = parser.parse_args()
    evidence = open(args.evidence, 'w', encoding='utf-8') if args.evidence else None
    with connect() as transport:
        if args.upload:
            with paramiko.SFTPClient.from_transport(transport) as sftp:
                sftp.put(args.upload, '/tmp/solbot-p1-package.tar.gz')
                sftp.chmod('/tmp/solbot-p1-package.tar.gz', 0o600)
        channel = transport.open_session(timeout=20)
        channel.exec_command('sudo -n /bin/bash -s')
        channel.sendall(Path(args.script).read_bytes().replace(b'\r\n', b'\n'))
        channel.shutdown_write()
        import select
        while not channel.exit_status_ready() or channel.recv_ready() or channel.recv_stderr_ready():
            select.select([channel], [], [], 1)
            if channel.recv_ready():
                chunk = channel.recv(65536).decode(errors='replace')
                sys.stdout.write(chunk); sys.stdout.flush()
                if evidence:
                    evidence.write(chunk); evidence.flush()
            if channel.recv_stderr_ready():
                chunk = channel.recv_stderr(65536).decode(errors='replace')
                sys.stderr.write(chunk); sys.stderr.flush()
                if evidence:
                    evidence.write(chunk); evidence.flush()
        if evidence:
            evidence.close()
        return channel.recv_exit_status()

if __name__ == '__main__':
    raise SystemExit(main())
