from ipaddress import ip_address
import os


def client_ip(request):
    address = request.META.get('REMOTE_ADDR', '')
    if os.environ.get('DJANGO_TRUST_PROXY') == '1':
        # Nginx must overwrite X-Real-IP and the application must bind to loopback.
        address = request.META.get('HTTP_X_REAL_IP', address)
    try:
        return str(ip_address(address))
    except ValueError:
        return None
