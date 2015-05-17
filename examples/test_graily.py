from graily import Graily, HTTPResponse, template, StaticFileHandler, Concurrent, init_log
import time

def main():
    import logging
    init_log(level=logging.DEBUG)

    class Temp(HTTPResponse):
        def get(self):
            return template("graily_test.html",
                    {'parameters': self.parameters, 'name': 'Graily'})

    class Test(HTTPResponse):
        def get(self, name):
            return "hello, {}".format(name)

        def post(self, name):
            return template("graily_test.html",
                    parameters=self.parameters, name=name)

    class ConTest(HTTPResponse):
        @Concurrent.register
        def get(self):
            time.sleep(5)  # won't block the main Thread
            return "hello from Graily concurrent"

    app = Graily([
            (r'^/temp/$', Temp),
            (r'^/static/(.*)$', StaticFileHandler.set_path("static")),
            (r'^/con/$', ConTest),
            (r'^/(.*)/$', Test),
        ])
    app.server_bind(("", 8888))
    app.serve_forever()

if __name__ == '__main__':
    main()
