import http.server, os, socketserver, sys
ROOT = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))), "backend", "static")
BENCH = os.path.dirname(os.path.abspath(__file__))

class H(http.server.SimpleHTTPRequestHandler):
    def translate_path(self, path):
        path = path.split("?")[0].split("#")[0]
        if path.startswith("/static/"):
            return os.path.join(ROOT, path[len("/static/"):])
        return os.path.join(BENCH, path.lstrip("/"))
    def log_message(self, *a):
        pass

socketserver.TCPServer.allow_reuse_address = True
with socketserver.TCPServer(("127.0.0.1", int(sys.argv[1])), H) as httpd:
    httpd.serve_forever()
