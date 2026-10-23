"""Direct LAN access with interface and same-origin checks; no access code or login."""
import ipaddress
import json
import subprocess
from urllib.parse import urlsplit


def lan_urls(port):
    """Read current interface addresses; never infer the Jetson IP from a remote host."""
    try:
        result = subprocess.run(['ip', '-j', '-4', 'address', 'show', 'up'],
                                capture_output=True, text=True, timeout=2, check=True)
        addresses = json.loads(result.stdout)
        return sorted({f"http://{entry['local']}:{port}" for device in addresses
                       if not device['ifname'].startswith(('lo', 'docker', 'br-', 'veth'))
                       for entry in device.get('addr_info', []) if entry.get('scope') == 'global'})
    except (OSError, ValueError, subprocess.SubprocessError, KeyError):
        return []


class AccessPolicy:
    def __init__(self, port, lan=False):
        self.port, self.lan = port, lan

    def valid_host(self, host, local_address):
        try:
            value = urlsplit('http://' + (host or ''))
            return (not value.username and not value.password and not value.path
                    and not value.query and not value.fragment
                    and (value.port or 80) == self.port
                    and (value.hostname == local_address or
                         (ipaddress.ip_address(local_address).is_loopback and value.hostname == 'localhost')))
        except ValueError:
            return False

    def authorized(self, client_address):
        return ipaddress.ip_address(client_address).is_loopback or self.lan


class AccessHandlerMixin:
    """Allow direct LAN requests; prevent foreign sites from issuing controls."""
    access_policy = None

    def end_headers(self):
        self.send_header('X-Frame-Options', 'DENY')
        self.send_header('Referrer-Policy', 'no-referrer')
        super().end_headers()

    def access_reply(self, code, data, ctype='text/plain; charset=utf-8'):
        self.send_response(code)
        self.send_header('Content-Type', ctype)
        self.send_header('Content-Length', str(len(data)))
        self.send_header('Cache-Control', 'no-store')
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.end_headers()
        self.wfile.write(data)

    def access_guard(self):
        policy = self.access_policy
        host = self.headers.get('Host')
        if len(self.headers.get_all('Host', [])) != 1 or not policy.valid_host(host, self.connection.getsockname()[0]):
            self.access_reply(403, b'Invalid host'); return False
        if self.command == 'POST':
            if (len(self.headers.get_all('Origin', [])) != 1 or
                    self.headers.get('Origin') != 'http://' + host):
                self.access_reply(403, b'Invalid origin'); return False
        if policy.authorized(self.client_address[0]):
            return True
        self.access_reply(403, b'LAN access is disabled')
        return False
