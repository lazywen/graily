from graily import Graily, HTTPResponse, template, StaticFileHandler, Concurrent, init_log
import tornado
from tornado import wsgi, httpserver

def main():
    import logging
    init_log(level=logging.DEBUG)

    class Test(HTTPResponse):
        def get(self):
            return "hello from Graily and tornado WSGIServer"

    app = Graily([(r'^.*$', Test)])
    container = wsgi.WSGIContainer(app)
    http_server = httpserver.HTTPServer(container)
    http_server.listen(8888)
    tornado.ioloop.IOLoop.instance().start()

if __name__ == '__main__':
    main()
