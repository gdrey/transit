"""HTTPS transport for hosted imports and user-configured model calls."""
import http.client
import ipaddress
import socket
import urllib.request

class PublicHTTPSConnection(http.client.HTTPSConnection):
    def connect(self):
        if self.port != 443:
            raise ValueError('Use the default HTTPS port.')
        addresses = socket.getaddrinfo(self.host, self.port, type=socket.SOCK_STREAM)
        if not addresses or any(not ipaddress.ip_address(item[4][0]).is_global for item in addresses):
            raise ValueError('Use an endpoint on the public internet.')
        last_error = None
        for family, kind, proto, _, target in addresses:
            connection = socket.socket(family, kind, proto)
            connection.settimeout(self.timeout)
            try:
                connection.connect(target)
                self.sock = self._context.wrap_socket(connection, server_hostname=self.host)
                return
            except OSError as error:
                last_error = error
                connection.close()
        raise last_error or OSError('Could not connect to the remote endpoint.')

class PublicHTTPSHandler(urllib.request.HTTPSHandler):
    def https_open(self, request):
        return self.do_open(PublicHTTPSConnection, request, context=self._context)

class RejectHTTP(urllib.request.HTTPHandler):
    def http_open(self, request):
        raise ValueError('The hosted workbench requires HTTPS.')

def opener(original, *handlers):
    return original(urllib.request.ProxyHandler({}), RejectHTTP(), PublicHTTPSHandler(), *handlers)
