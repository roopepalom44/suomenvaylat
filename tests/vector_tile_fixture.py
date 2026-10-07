"""Tiny real MVT road and Mapbox style, served locally for native smoke tests."""
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import threading


def varint(value):
    result = bytearray()
    while value > 127:
        result.append((value & 127) | 128)
        value >>= 7
    result.append(value)
    return bytes(result)


def field(number, data):
    return varint(number * 8 + 2) + varint(len(data)) + data


geometry = b"".join(varint(x) for x in (9, 0, 0, 10, 8192, 8192))
feature = varint(8) + varint(1) + varint(24) + varint(2) + field(4, geometry)
TILE = field(3, field(1, b"roads") + field(2, feature) + varint(40) + varint(4096) + varint(120) + varint(2))


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def do_GET(self):
        base = "http://127.0.0.1:" + str(self.server.server_port)
        if self.path.split("?")[0].endswith(".pbf"):
            body, mime = TILE, "application/x-protobuf"
        else:
            if self.path.startswith("/style"):
                data = {"version": 8, "sources": {"demo": {"type": "vector", "tiles": [base + "/{z}/{x}/{y}.pbf"], "minzoom": 0, "maxzoom": 16}},
                        "layers": [{"id": "roads", "type": "line", "source": "demo", "source-layer": "roads",
                                    "paint": {"line-color": "#ee0000", "line-width": 3}}]}
            else:
                data = {"tilejson": "2.2.0", "tiles": [base + "/{z}/{x}/{y}.pbf"], "minzoom": 0, "maxzoom": 16,
                        "vector_layers": [{"id": "roads", "fields": {}}]}
            body, mime = json.dumps(data).encode(), "application/json"
        self.send_response(200)
        self.send_header("Content-Type", mime)
        self.end_headers()
        self.wfile.write(body)


def start_server():
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server, "http://127.0.0.1:" + str(server.server_port)
