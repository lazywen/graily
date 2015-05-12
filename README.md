# Graily Web Server
Graily is a no-blocking web server and micro web-framework written by Python. It's only support for Python3 and Linux platform.

* **Lightweight And Fast :** It's has no dependencies other than the standard library. And it's fast.
* **Long-Polling :** Graily can handle tens of thousands of connections for long polling.
* **WSGI Server Support :** Graily has support for WSGI, so it can be used as a HTTP server for any other frameworks.
* **Concurrent :** Graily can chose to use Concurrent class to handle those request which may blocking the main thread.

#####Here is a sample "Hello, world" example:
```python
from graily import Graily, HTTPResponse

class MainHandler(HTTPResponse):
    def get(self):
        return "Hello, world"
            
server = Graily([
        (r'^/$', MainHandler),
    ])  
server.server_bind(("", 8888))
server.serve_forever()
```
#####Concurrent example:
```python
from graily import Graily, HTTPResponse, Concurrent
import time

class MainHandler(HTTPResponse):
    @Concurrent.register
    def get(self):
        time.sleep(5)  # won't blocking the main Thread
        return "Hello, world"
            
server = Graily([
        (r'^/$', MainHandler),
    ])  
server.server_bind(("", 8888))
server.serve_forever()
```
#####WSGI server with Bottle Application example:
```python
from bottle import route, default_app
from graily import make_server

@route('/')
def main():
    return 'hello from bottle.'

application = default_app()
server = make_server(('', 8888), application)
server.serve_forever() 
```
You can find more examples (concurrent, template) in [examples](https://github.com/lazywen/graily/tree/master/examples).