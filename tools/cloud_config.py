"""Public endpoint defaults; workspace-specific configuration stays in the environment."""
import os
from urllib.parse import urlsplit

DEFAULT_API_HOST = 'https://dashscope.aliyuncs.com'


def api_host(environ=None):
    env = os.environ if environ is None else environ
    value = env.get('LUMINA_API_HOST', DEFAULT_API_HOST).strip().rstrip('/')
    try:
        parsed = urlsplit(value)
        port = parsed.port
        valid = (parsed.scheme == 'https' and parsed.hostname
                 and parsed.username is None and parsed.password is None
                 and not parsed.path and not parsed.query and not parsed.fragment
                 and port in (None, 443) and not any(char.isspace() for char in value))
    except ValueError:
        valid = False
    if not valid:
        raise ValueError('LUMINA_API_HOST must be an HTTPS origin without credentials or an API path')
    return value
