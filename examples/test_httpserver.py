from graily import HTTPServer, BaseHTTPRequestHandler, init_log

def main():
    import logging
    init_log(level=logging.DEBUG)

    class Test(BaseHTTPRequestHandler):
        def GET(self):
            return "hello from graily httpserver"
        def POST(self):
            return str(self.parameters)

    server = HTTPServer(("", 8888), Test)
    server.serve_forever()

if __name__ == '__main__':
    main()
