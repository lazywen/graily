from graily import make_server, init_log
from bottle import route, default_app
import logging

@route("/")
def test():
    return "hello from bottle with Graily"

def main():
    init_log(level=logging.DEBUG)
    app = default_app()
    server = make_server(("", 8888), app)
    server.serve_forever()

if __name__ == '__main__':
    main()
