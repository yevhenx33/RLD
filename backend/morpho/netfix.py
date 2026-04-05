"""Force IPv4 for all outbound connections (Docker IPv6 issue workaround)."""
import socket

_orig_getaddrinfo = socket.getaddrinfo

def _ipv4_getaddrinfo(*args, **kwargs):
    responses = _orig_getaddrinfo(*args, **kwargs)
    return [r for r in responses if r[0] == socket.AF_INET] or responses

socket.getaddrinfo = _ipv4_getaddrinfo
