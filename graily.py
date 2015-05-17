#!/usr/bin/env python3
# -*- coding: utf-8 -*-

__all__ = [
        # tcp server
        'TCPServer', 'ThreadPollTCPServer', 'StreamRequestHandler',
        # http server
        'HTTPServer', 'ThreadPollHTTPServer', 'BaseHTTPRequestHandler',
        'HTTPResponse', 'StaticFileHandler'
        # wsgi server
        'WSGIServer', 'WSGIRequestHandler', 'make_server',
        # templates
        'BaseTemplate', 'templates', 'MakoTemplate',
        # main application and others
        'Graily', 'Concurrent', 'init_log'
    ]

import os, sys, re, errno, time, socket, select, logging, random, types, io
import mimetypes, functools, heapq
import traceback
assert sys.version_info>=(3,0,0), "Only support for Python3"

import queue
import _thread, threading

from select import epoll
from urllib.parse import urlparse, parse_qs

class GrailyPoll:
    '''main Poll loop'''

    READ = select.EPOLLIN
    WRITE = select.EPOLLOUT
    ERROR = select.EPOLLERR | select.EPOLLHUP
    MAX_KEEPALIVE_TIME = 300

    server_name = "Graily"

    def __init__(self, server_address, RequestHandler, allow_reuse_address=True,
            max_input_size=2097152):
        self.server_address = server_address
        self.RequestHandler = RequestHandler
        self.allow_reuse_address = allow_reuse_address
        self.max_input_size = max_input_size
        self.socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._sockets = {self.socket.fileno(): self.socket}
        self._shutdown_request = True
        self._handlers = {}
        self._keepalive = {}

        self.server_bind()

    def init_socket(self):
        logging.info('Starting server at {}'.format(self.server_address))
        if self.allow_reuse_address:
            self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.socket.bind(self.server_address)
        self.server_address = self.socket.getsockname()
        self.socket.setblocking(False)
        self.socket.listen(100)
        self.init_epoll()

    def init_epoll(self):
        self.epoll = epoll()
        self.epoll.register(self.socket, self.READ | self.ERROR)

    def server_bind(self):
        self.init_socket()
        self.init_epoll()

    def server_close(self):
        self.socket.close()

    def shutdown_request(self, request, flag=socket.SHUT_WR):
        try: request.shutdown(flag)
        except OSError: pass

    def close_request(self, request):
        try: request.close()
        except OSError: pass

    def _run(self, timeout=1):
        # TODO timeout

        try:
            event_pairs = self.epoll.poll(timeout)
            # logging.debug("event: {}".format(event_pairs))
            # logging.debug("sockets: {}".format([i for i in self._sockets]))
            # logging.debug("handlers: {}".format([(i.fileno(), self._handlers[i]) for i in self._handlers ]))
        except Exception as e:
            if errno_from_exception(e) == errno.EINTR:
                return
            else: raise

        for fd, event in event_pairs:
            sock = self._sockets[fd]
            if event & self.ERROR:
                self.epoll.unregister(fd)
                del self._sockets[fd]
                self._handlers.pop(sock, None)
                self._keepalive.pop(sock, None)
                self.close_request(sock)
            else:
                try:
                    if sock is self.socket:
                        if not self._shutdown_request:
                            request, client_address = sock.accept()
                            request.setblocking(False)
                            _fd = request.fileno()
                            self._sockets[_fd] = request
                            self.epoll.register(request, self.READ | self.ERROR)
                            self._keepalive[request] = time.time()
                        # TODO wait threads exit
                        else: time.sleep(timeout)

                    elif event & self.READ:
                        self._keepalive[sock] = time.time()
                        self.handle_request(sock)
                    elif event & self.WRITE:
                        self.handle_response(sock)

                except (OSError, IOError) as e:
                    if errno_from_exception(e) == errno.EPIPE:
                        # Happens when the client closes the connection
                        pass
                except Exception as e:
                    self.handle_exception(e, sock)

        # do something when no events
        if len(event_pairs) == 0:
            for _fd, _sock in self._sockets.items():
                if _sock not in self._keepalive:
                    self._keepalive[_sock] = time.time()
            for _sock, _time in self._keepalive.items():
                if time.time()-_time >= self.MAX_KEEPALIVE_TIME:
                    self.shutdown_request(_sock)

    def handle_exception(self, err, sock):
        logging.exception('')
        if sock is self.socket: time.sleep(1)
        else: self.shutdown_request(sock)

    def handle_request(self, sock):
        if sock not in self._handlers:
            self._handlers[sock] = self.RequestHandler(sock, self)
        self._handlers[sock]._run()

    def handle_response(self, sock):
        handler = self._handlers[sock]
        sent = sock.send(handler.iostream._write_buffer)
        # logging.debug('epoll sent: %s' % sent)
        if sent >= len(handler.iostream._write_buffer):
            self.update_handler(sock, self.READ)
        handler.iostream._write_buffer = handler.iostream._write_buffer[sent:]

    def update_handler(self, sock, event):
        self.epoll.modify(sock, event | self.ERROR)


class TCPServer(GrailyPoll):
    '''tcp server'''
    def __init__(self, server_address, RequestHandler, **kwargs):
        super(TCPServer, self).__init__(server_address, RequestHandler,
                **kwargs)

    def serve_forever(self):
        self._shutdown_request = False
        while not self._shutdown_request or len(self.sockets)>1:
            self._run()

class ThreadPollTCPServer(TCPServer):
    '''use threading poll for handler'''
    def __init__(self, server_address, RequestHandler,
                poll_size=100, max_tasks=1000, **kwargs):
        super(ThreadPollTCPServer, self).__init__(server_address, RequestHandler, **kwargs)
        if Concurrent._concurrency:
            self.concurrent = Concurrent(poll_size, max_tasks, self)

    def put_task(self, task):
        self.concurrent.tasks.put(task)

    def serve_forever(self):
        if Concurrent._concurrency: self.concurrent.start()
        self._shutdown_request = False
        while not self._shutdown_request or len(self.sockets)>1:
            self._run()

class HTTPServer(TCPServer):
    '''HTTP server'''

class ThreadPollHTTPServer(ThreadPollTCPServer):
    '''HTTPServer use threading poll for handler'''

class WSGIServer(HTTPServer):
    application = None
    def server_bind(self):
        """Override server_bind to store the server name."""
        HTTPServer.server_bind(self)
        self.setup_environ()

    def setup_environ(self):
        # Set up base environment
        env = self.base_environ = {}
        env['SERVER_NAME'] = self.server_name
        env['GATEWAY_INTERFACE'] = 'CGI/1.1'
        env['SERVER_PORT'] = str(self.server_address[1])
        env['REMOTE_HOST']=''
        env['CONTENT_LENGTH']=''
        env['SCRIPT_NAME'] = ''

    def get_app(self):
        return self.application

    def set_app(self,application):
        self.application = application

class Concurrent:
    # when _concurrency is True, the server will start thread poll
    _concurrency = False

    def __init__(self, poll_size, max_tasks, server):
        self.poll_size = poll_size
        self.tasks = queue.Queue(max_tasks)
        self.server = server
        self._thread_poll = []
        self._running = False

    def start(self):
        logging.info('starting thread poll ...')
        if self._running:
            logging.warning('thread poll already started!')
            return

        def worker():
            while True:
                task = self.tasks.get()
                # logging.debug(threading.current_thread().getName()+' got a task: '+str(task))
                # time.sleep(random.randint(1,5)/10)

                next(task['run'])
                try: res = task['run'].send(task['args'])
                except StopIteration: pass
                except Exception as e:
                    self.server.handle_exception(e, task['socket'])
                else:
                    if res and 'result_handler' in task:
                        task['result_handler'](res)
                    if 'callback' in task and len(task['callback'])>0:
                        for callback, args in task['callback']:
                            callback(*args)

        for i in range(self.poll_size):
            td = threading.Thread(target=worker, args=())
            td.setDaemon(True)
            td.start()
            self._thread_poll.append(td)
        self._running = True

    def allocate(self):
        # TODO
        pass

    class register:
        '''
        the register decorator can make the method concurrent:

        @Concurrent.register
        def get(self):
            self.write("hello, world")

        '''

        def __init__(self, func):
            self.func = func
            self.__dict__['_graily_concurrency'] = True
            if not Concurrent._concurrency:
                Concurrent._concurrency = True
        def __call__(self, *args, **kwargs):
            ora_args = yield
            new_args = []
            if ora_args: new_args.extend(ora_args)
            new_args.extend(args)
            yield self.func(*new_args, **kwargs)

class BaseRequestHandler:
    '''
    The base request handler class, you must implemente those method:

        parse_request
        verify_request
        dataReceived
    '''

    def __init__(self, request, server):
        self.request = request
        self.server = server
        self.iostream = BaseIOStream(request, server)
        self.response_handler = self.dataReceived
        self._initialize()

    def _initialize(self):
        pass

    def write(self, res):
        if not self.iostream.write(res):
            self.server.update_handler(self.request, self.server.WRITE)

    def close(self):
        self.server.shutdown_request(self.request)

    def _run(self):
        data_ready = self.iostream.read()
        if data_ready and self.verify_request():
            if self.parse_request():
                prg = self.dataReceived()
                if isinstance(prg, types.GeneratorType) and '_graily_concurrency' \
                        in self.dataReceived.__dict__:
                    task = {'socket': self.request, 'run': prg, 'args':(self,),
                                    'callback': [(self._initialize, ())]}
                    self.server.put_task(task)
                else: self._initialize()


    def parse_request(self):
        '''pop data that you need from iostream._read_buffer and keep the
            others.
        '''
        raise NotImplementedError

    def verify_request(self):
        ''' verify the received data with your own protocol, return True
            if the data is correct and a False value if not correct.
        '''
        raise NotImplementedError

    def dataReceived(self):
        raise NotImplementedError

class StreamRequestHandler(BaseRequestHandler):
    ''' The request handler for TCPServer '''
    def _initialize(self):
        pass

    def parse_request(self):
        self.data = self.iostream._read_buffer.decode()
        self.iostream._read_buffer = b""
        return True

    def verify_request(self):
        return bool(self.iostream._read_buffer)

class BaseHTTPRequestHandler(BaseRequestHandler):
    ''' The request hansler for HTTPServer '''

    SUPPORT_HTTP_VERSION = ('HTTP/1.0', 'HTTP/1.1')
    HTTP_METHOD = ('HEAD', 'GET', 'POST', 'OPTIONS', 'PUT', 'DELETE', 'TRACE', 'CONNECT')
    ERROR_MESSAGE_TEMPLATE = ('<html>'
        '<head><title>%(code)d %(msg)s</title></head>'
        '<center><h1>%(code)d %(msg)s</h1></center>'
        '<center><p>%(desc)s</p></center>'
        '</body>'
        '</html>')
    DEBUG_MESSAGE_TEMPLATE = ()

    def _initialize(self):
        self._send_buffer = b""
        self.keep_alive = False
        self.command = ""
        self.host = ""
        self.parameters = {}
        self.request_url = ""
        self.request_head = None
        self.request_body = None
        self.request_path = ""
        self.request_query = ""
        self.http_version = ""
        self.headers = {}
        self.respond_status = 200
        self.request_options = {}
        self.header_sent = False
        self.environ = {'write':self.write, 'send_error':self.send_error,
            'set_header':self.set_header, 'set_response_status':self.set_response_status}

    def verify_request(self):
        # return b'HTTP/1.' in self.iostream._read_buffer and \
        #         b'\r\n\r\n' in self.iostream._read_buffer
        return b'\r\n\r\n' in self.iostream._read_buffer

    def parse_request(self):
        self.data = self.iostream._read_buffer.decode()
        self.iostream._read_buffer = b""
        slices = self.data.split('\r\n\r\n')
        if len(slices) > 2:
            self.send_error(400)
            return False
        self.environ['request_head'] = self.request_head = slices[0]
        self.environ['request_body'] = self.request_body = slices[1]

        request_head = io.StringIO(self.request_head)
        request_line = request_head.readline().rstrip('\r\n')
        args = request_line.split()
        if len(args) == 3:
            self.command = args[0]
            self.request_url = args[1]
            self.http_version = args[2]

            _urlpar = urlparse(self.request_url)
            self.environ['request_path'] = self.request_path = _urlpar.path
            self.environ['request_query'] = self.request_query = _urlpar.query

            if self.http_version not in self.SUPPORT_HTTP_VERSION:
                self.send_error(505, "HTTP Version {} Not Supported".format(self.http_version))
                return False
            if self.command not in self.HTTP_METHOD:
                self.send_error(405, "Not Allowed: {}".format(self.command))
                return False

            while True:
                line = request_head.readline()
                if not line: break
                pos = line.find(':')
                if pos < 0:
                    self.send_error(400); return False
                self.request_options[line[0:pos].strip()] = line[pos+1:].strip()

            if 'Host' not in self.request_options:
                self.send_error(400); return False
            if self.request_options.get('Connection', '').lower() == 'keep-alive':
                self.keep_alive = True

            if self.command == 'GET':
                self.parameters = parse_qs(self.request_query)
            elif self.command == 'POST':
                self.parameters = parse_qs(self.request_body)
            if self.parameters:
                for key, val in self.parameters.items():
                    if type(val)==list and len(val)==1:
                        self.parameters[key] = val[0]
            self.environ['parameters'] = self.parameters
            return True

        elif len(args) == 2:
            # HTTP/0.9
            self.send_error(400, 'not support')
        else: self.send_error(400, "bad request syntax")
        return False

    def set_response_status(self, code):
        assert type(code) == int
        self.respond_status = code
    def set_header(self, opt, val):
        self.headers[opt] = val

    def send_response(self, body=None):
        if not self.header_sent:
            self._send_buffer = ("{} {} {}\r\n".format(self.http_version, self.respond_status, \
                self.RESPONSES_CODE.get(self.respond_status, '???'))).encode('latin-1', 'strict')
            self.respond_status = 200
            for opt, val in self.headers.items():
                self._send_buffer += ("{}: {}\r\n".format(opt, val)).encode('latin-1', 'strict')
            self._send_buffer += b'\r\n'
            self.headers = {}
            self.header_sent = True
        _data = self._send_buffer
        if type(body)==str:
            body = body.encode('utf-8', 'replace')
        if body: _data += body
        self._write(_data)
        self._send_buffer = b""

    def send_error(self, code, desc=""):
        msg = self.RESPONSES_CODE.get(code)
        body = (self.ERROR_MESSAGE_TEMPLATE % {'code':code, 'msg':msg, 'desc': desc})
        self.set_response_status(code)
        self.set_header("Content-Type", "text/html; charset=UTF-8")
        self.set_header('Connection', 'close')
        self.set_header('Content-Length', int(len(body)))
        self.send_response(body)
        # TODO
        self.server.shutdown_request(self.request)

    def _write(self, res):
        if not self.iostream.write(res):
            self.server.update_handler(self.request, self.server.WRITE)

    def write(self, data, finish=False):
        '''support HTTP long polling'''
        # assert type(data) == str
        self.set_header('Connection', 'keep-alive')
        if finish and "Content-Length" not in self.headers:
            try: data_length = len(data)
            except: pass
            else: self.set_header('Content-Length', str(data_length))
        if "Content-Type" not in self.headers:
            self.set_header("Content-Type", "text/html; charset=UTF-8")
        if type(data) == types.GeneratorType:
            headers_sent = False
            for _d in data:
                if type(_d)==str: _d=_d.encode('utf-8','replace')
                if not headers_sent:
                    self.send_response()
                    headers_sent = True
                if _d: self._write(_d)
        else:
            self.send_response(data)

    def _run(self):
        data_ready = self.iostream.read()
        if data_ready and self.verify_request():
            if self.parse_request():
                self.dataReceived()

    def dataReceived(self):
        _concurrency = False
        if self.command == 'HEAD':
            self.write('')
        elif self.command in ('GET', 'POST'):
            if hasattr(self, 'get_handler'):
                handler, args = self.get_handler(self.request_path)
                ins = handler(self.environ)
                if hasattr(ins, self.command.lower()):
                    func = getattr(ins, self.command.lower())
                    res = func(*args)
                    if type(res) == types.GeneratorType and '_graily_concurrency' \
                            in func.__dict__:
                        _concurrency = True
                        task = {'socket': self.request, 'run': res, 'args':(self,),
                                'callback': [(self._initialize, ())],
                                'result_handler':functools.partial(self.write, finish=True)}
                        self.server.put_task(task)
                    else:
                        if res: self.write(res, finish=True)
                else: self.send_error(501, "{} Method Not Implemented".format(self.command))
            else:
                if hasattr(self, self.command):
                    res = getattr(self, self.command)()
                    if res: self.write(res, finish=True)
                else: self.send_error(501, "{} Method Not Implemented".format(self.command))

        if not _concurrency:
            if not self.keep_alive:
                self.server.shutdown_request(self.request)
            self._initialize()

    RESPONSES_CODE = {
        100: 'Continue', 101: 'Switching Protocols',

        200: 'OK', 201: 'Created', 202: 'Accepted', 203: 'Non-Authoritative Information',
        204: 'No Content', 205: 'Reset Content', 206: 'Partial Content',

        300: 'Multiple Choices', 301: 'Moved Permanently', 302: 'Found', 303: 'See Other',
        304: 'Not Modified', 305: 'Use Proxy', 307: 'Temporary Redirect',

        400: 'Bad Request', 401: 'Unauthorized', 402: 'Payment Required', 403: 'Forbidden',
        404: 'Not Found', 405: 'Method Not Allowed', 406: 'Not Acceptable',
        407: 'Proxy Authentication Required', 408: 'Request Timeout', 409: 'Conflict',
        410: 'Gone', 411: 'Length Required', 412: 'Precondition Failed',
        413: 'Request Entity Too Large', 414: 'Request-URI Too Long', 415: 'Unsupported Media Type',
        416: 'Requested Range Not Satisfiable', 417: 'Expectation Failed', 428: 'Precondition Required',
        429: 'Too Many Requests', 431: 'Request Header Fields Too Large',

        500: 'Internal Server Error', 501: 'Not Implemented', 502: 'Bad Gateway',
        503: 'Service Unavailable', 504: 'Gateway Timeout', 505: 'HTTP Version Not Supported',
        511: 'Network Authentication Required',
        }

class HTTPResponse:
    '''
    HTTPResponse is use for handle HTTP method:

    class MainHanlder(HTTPResponse):
        def get(self):
            self.write("hello, world")
    '''

    def __init__(self, environ):
        self.base_environ = environ.copy()
        self.headers = {}
        self.init_environ()
    def init_environ(self):
        for k,v in self.base_environ.items():
            setattr(self, k ,v)

class NotFoundHandler(HTTPResponse):
    code = 404
    def get(self, *args):
        return self.send_error(404, "not found for: {}".format(self.request_path))
    def post(self, *args):
        return self.get(*args)

class StaticFileHandler(HTTPResponse):
    static_path = None
    def get(self, rel_path):
        if not self.static_path:
            raise ValueError("static_path not set!")
        if rel_path.endswith('/'):
            return self.send_error(403, "Your Request Is Forbidden: {}".format(rel_path))
        path = os.path.join(self.static_path, rel_path)
        if os.path.isfile(path):
            self.set_response_status(200)
            extension = os.path.splitext(path)[1].lower()
            ctype = self.extensions_map.get(extension, self.extensions_map[''])
            self.set_header("Content-Type", ctype)
            # not allowed hop-by hop
            # self.set_header("Connection", "close")
            f = open(path, 'rb')
            fs = os.fstat(f.fileno())
            self.set_header("Content-Length", str(fs[6]))

            # TODO Last-Modified, and return 302 if not modified
            # self.set_header("Last-Modified", self.format_time(fs.st_mtime))

            # read all contents to memory when request a small file
            if fs[6] < 102400: return f.read()
            else: return self.yield_file(f)
        else: return self.send_error(404, "Not Found: {}".format(rel_path))

    def yield_file(self, fd):
        chunk_size = 61440 # <65535
        _c = fd.read(chunk_size)
        while _c:
            yield _c
            _c = fd.read(chunk_size)

    @classmethod
    def set_path(cls, path):
        full_path = os.path.join(os.path.dirname(
            os.path.abspath(__file__)), path)
        if os.path.isdir(full_path):
            StaticFileHandler.static_path = full_path
        else: raise ValueError("no such path: {}".format(full_path))
        return cls

    if not mimetypes.inited:
        mimetypes.init() # try to read system mime.types
    extensions_map = mimetypes.types_map.copy()
    extensions_map.update({
        '': 'application/octet-stream', # Default
        '.py': 'text/plain',
        '.c': 'text/plain',
        '.h': 'text/plain',
        })

class WSGIServerHandler:
    def __init__(self, request_handler, stdin, stdout, stderr, environ,
        multithread=True, multiprocess=False):
        self.request_handler = request_handler
        self.stdin = stdin
        self.stdout = stdout
        self.stderr = stderr
        self.base_env = environ
        self.wsgi_multithread = multithread
        self.wsgi_multiprocess = multiprocess
        self.request_handler = None
        self.headers_sent = False
        self._send_buffer = b""

    def setup_environ(self):
        env = self.environ = self.base_env.copy()
        env['wsgi.input'] = self.stdin
        env['wsgi.errors'] = self.stderr
        # env['wsgi.version'] = self.wsgi_version
        # env['wsgi.run_once'] = self.wsgi_run_once
        env['wsgi.multithread']= self.wsgi_multithread
        env['wsgi.multiprocess'] = self.wsgi_multiprocess

    def handle_error(self, e):
        self.request_handler.server.handle_exception(e,
                self.request_handler.request)

    def run(self, application):
        try:
            self.setup_environ()
            self.result = application(self.environ, self.start_response)
            self.finish_response()
        except Exception as e:
            self.handle_error(e)

    def start_response(self, status, headers):
        self.status = status.strip()
        self.headers = self.format_headers(headers)
        self.headers.update(self.request_handler.headers)

        assert type(status)==str, "Status must a str type"
        assert len(status)>=4,"Status must be at least 4 characters"
        assert int(status[:3]),"Status message must begin w/3-digit code"
        assert status[3]==" ", "Status message must have a space after code"
        return self.write

    def finish_response(self):
        try:
            for data in self.result:
                if type(data) == str: data = data.encode('utf-8', replace)
                self._send_buffer += data
            self.write(self._send_buffer)
        finally:
            pass

    def format_headers(self, headers):
        return dict(list(headers))
    def set_header(self, key, val):
        self.headers[key] = val

    def send_headers(self):
        if 'Content-Length' not in self.headers:
            self.set_header('Content-Length', len(self._send_buffer))
        _headers = "{} {}\r\n".format(self.environ['SERVER_PROTOCOL'], self.status)
        for k, v in self.headers.items():
            _headers += "{}: {}\r\n".format(k, v)
        _headers += "\r\n"
        self._write(_headers)

    def close(self):
        self.request_handler.close()

    def _write(self, data):
        self.stdout.write(data)

    def write(self, data):
        if not self.headers_sent:
            self.send_headers()
        self._write(data)

    def flush(self):
        '''return True if all data has been sent'''
        return not bool(self.request_handler.iostream._write_buffer)

class WSGIRequestHandler(BaseHTTPRequestHandler):
    server_version = "WSGIServer/0.2"
    class STDOUT:
        def flush(self): pass
        def close(self): self.write = lambda d:0

    def get_environ(self):
        env = self.server.base_environ.copy()
        env['SERVER_PROTOCOL'] = self.http_version
        env['SERVER_SOFTWARE'] = self.server_version
        env['REQUEST_METHOD'] = self.command
        url_parse = urlparse(self.request_url)
        env['PATH_INFO'] = url_parse.path
        env['QUERY_STRING'] = url_parse.query
        env['wsgi.url_scheme']= url_parse.scheme
        env['HTTP_HOST']= url_parse.netloc
        return env

    def get_stderr(self):
        return sys.stderr

    def _run(self):
        data_ready = self.iostream.read()
        if data_ready and self.verify_request():
            if self.parse_request():
                # prg = self.dataReceived()
                stdout = self.STDOUT()
                setattr(stdout, 'write', self._write)
                handler = WSGIServerHandler(
                    self, io.StringIO(self.request_body), stdout, self.get_stderr(), self.get_environ()
                )
                handler.request_handler = self
                handler.run(self.server.get_app())
                if not self.keep_alive: self.close()

class WSGIAppHandler:
    def __init__(self, environ, get_handler):
        self.base_environ = environ.copy()
        self.get_handler = get_handler
        self._write = None
        self.handler = None
        self.result = []
        self.parameters = {}
        self.environ = {'set_header': self.set_header,
                'set_response_status':self.set_response_status}
        self.headers = {}
        self.respond_status = 200
        self.init_response()

    def set_header(self, opt, val):
        self.headers[opt] = val

    def set_response_status(self, code):
        assert type(code) == int
        self.respond_status = code

    def init_response(self):
        self.environ['request_path'] = self.request_path = self.base_environ['PATH_INFO']
        self.environ['request_version'] = self.request_version = self.base_environ['SERVER_PROTOCOL']
        self.environ['url_scheme'] = self.url_scheme = self.base_environ['wsgi.url_scheme']
        self.environ['request_query'] = self.request_query = self.base_environ['QUERY_STRING']
        self.environ['request_connection_type'] = self.request_connection_type = self.base_environ.get('HTTP_CONNECTION')
        self.environ['run_once'] = self.run_once = self.base_environ.get('wsgi.run_once')
        self.environ['multiprocess'] = self.multiprocess = self.base_environ.get('wsgi.multiprocess')
        self.environ['stdin'] = self.stdin = self.base_environ['wsgi.input']
        self.environ['host'] = self.host = self.base_environ['HTTP_HOST']
        self.environ['stderr'] = self.stderr = self.base_environ['wsgi.errors']
        self.environ['command'] = self.command = self.base_environ['REQUEST_METHOD']
        self.environ['multithread'] = self.multithread = self.base_environ.get('wsgi.multithread')

        if self.request_query:
            self.parameters = parse_qs(self.request_query)
        if self.command == 'POST':
            # TODO large body
            self.environ['request_body'] = self.request_body = self.stdin.read(65536)
            self.parameters.update(parse_qs(self.request_body))
        self.environ['parameters'] = self.parameters
        self.environ['write'] = self.write
        self.environ['send_error'] = self.send_error

    def write(self, data):
        if type(data) == str:
            data = data.encode('utf-8')
        self.result.append(data)

    def send_error(self, code, msg):
        self.set_response_status(code)
        self.stderr.write(msg)
        return msg

    def get_headers(self):
        if "Content-Type" not in self.headers:
            self.headers["Content-Type"] = "text/html; charset=UTF-8"
        return list(self.headers.items())

    def process(self):
        '''return (status, headers_list)'''
        handler, args = self.get_handler(self.request_path)
        if hasattr(handler, self.command.lower()):
            ins = handler(self.environ)
            res = getattr(ins, self.command.lower())(*args)
            if type(res) == types.GeneratorType:
                for _data in res: self.write(_data)
            else:
                if res: self.write(res)

            if hasattr(handler, 'code'):
                code = int(getattr(handler, 'code'))
                status = "{} {}".format(code, BaseHTTPRequestHandler.RESPONSES_CODE.get(code, "???"))
            else: status = "{} {}".format(self.respond_status,
                    BaseHTTPRequestHandler.RESPONSES_CODE.get(self.respond_status, "???"))
            headers = self.get_headers()
        else:
            status = "501 Not Implemented"
            headers = [('Content-Type', 'text/html; charset=UTF-8')]
        return status, headers

    def finish_response(self, write_func):
        '''return results_list'''
        if write_func: self._write = write_func
        return self.result

class BaseIOStream:
    MAX_READ_SIZE = 2097152
    MAX_CHUNK_SIZE = 65536
    _ERRNO_WOULDBLOCK = (errno.EWOULDBLOCK, errno.EAGAIN)
    if hasattr(errno, "WSAEWOULDBLOCK"):
        _ERRNO_WOULDBLOCK += (errno.WSAEWOULDBLOCK,)

    _ERRNO_CONNRESET = (errno.ECONNRESET, errno.ECONNABORTED, errno.EPIPE,
                errno.ETIMEDOUT)
    if hasattr(errno, "WSAECONNRESET"):
        _ERRNO_CONNRESET += (errno.WSAECONNRESET, errno.WSAECONNABORTED,
                errno.WSAETIMEDOUT)

    def __init__(self, request, server):
        self.request = request
        self.server = server
        self._read_buffer = b""
        self._write_buffer = b""

    def read(self):
        try: chunk = self.request.recv(self.MAX_CHUNK_SIZE)
        except (socket.error, IOError, OSError) as e:
            if errno_from_exception(e) in self._ERRNO_WOULDBLOCK:
                pass
            else:
                self.server.handle_exception(e, self.request)
                return False
        # TODO shutdown_request when buffer > MAX_READ_SIZE
        else:
            if chunk: self._read_buffer += chunk
            else:
                self.server.shutdown_request(self.request)
                return False
        return True

    def write(self, data):
        if type(data) == str: data = data.encode('utf-8')
        sent = 0
        while True:
            try: sent = self.request.send(data)
            except (socket.error, IOError, OSError) as e:
                if errno_from_exception(e) in self._ERRNO_WOULDBLOCK:
                    self._write_buffer += data
                    return False
                else:
                    self.server.handle_exception(e, self.request)
                    return True
            else:
                # logging.debug('directly sent: {}'.format(sent))
                if sent < len(data): data = data[sent:]
                else: return True

class DictHeapq:
    class _node(object):
        def __init__(self, k, v, f): self.k, self.v, self.f = k, v, f
        def __cmp__(self, o): return self.f > o.f
        def __lt__(self, o): return self.f < o.f
        def __eq__(self, o): return self.f == o.f
    def __init__(self, size):
        self.size, self.f = size, 0
        self._dict, self._heap = {}, []
    def __contains__(self, k): return k in self._dict
    def __setitem__(self, k, v):
        if k in self._dict:
            n = self._dict[k]
            n.v = v
            self.f += 1
            n.f = self.f
            heapq.heapify(self._heap)
        else:
            while len(self._heap) >= self.size:
                del self._dict[heapq.heappop(self._heap).k]
                self.f = 0
                for n in self._heap: n.f = 0
            n = self._node(k, v, self.f)
            self._dict[k] = n
            heapq.heappush(self._heap, n)
    def __getitem__(self, k):
        n = self._dict[k]
        self.f += 1
        n.f = self.f
        heapq.heapify(self._heap)
        return n.v
    def __delitem__(self, k):
        n = self._dict[k]
        del self._dict[k]
        self._heap.remove(n)
        heapq.heapify(self._heap)
        return n.v
    def __iter__(self):
        c = self._heap[:]
        while len(c): yield heapq.heappop(c).k
        raise StopIteration

def cache_template(func):
    _cache = {}
    # use DictHeapq as cache for a large mount templates
    # _cache = DictHeapq(1000)

    def _(self, *args, **kwargs):
        tplid = (str(self.lookup.sort()), self.filename)
        if tplid in _cache:
            res = _cache[tplid]
        else:
            res = func(self, *args, **kwargs)
            _cache[tplid] = res
        return res
    return _

class BaseTemplate:
    pattern = re.compile('{{(.*?)}}')
    def __init__(self, source=None, filename=None, lookup=['templates'], **kwargs):
        self.source = source
        self.filename = filename
        self.lookup = lookup
        self.kwargs = kwargs
        self.encoding = 'utf-8'
        self.env = {}
        self.prepare()

    def prepare(self, **kwargs):
        if not self.source:
            self.source = self.get_source()

    @cache_template
    def get_source(self):
        path = ""
        for tpl_path in self.lookup:
            full_path = os.path.join(os.path.dirname(
                os.path.abspath(__file__)), tpl_path, self.filename)
            if os.path.isfile(full_path):
                path = full_path
                break
        if not path:
            raise ValueError("can't find template: {}".format(self.filename))
        source = open(path, 'rb').read().decode('utf-8')
        return source

    def repl(self, match):
        code = match.group(1).strip()
        if " for " in code: code = "[{}]".format(code)
        if code:
            res = eval(code, self.env)
            if type(res) == list: res = "".join(map(str, res))
            else: res = str(res)
            return res

    def render(self, *args, **kwargs):
        # return "good"
        self.env = {}
        if type(args[0]) == dict: self.env.update(args[0])
        self.env.update(kwargs)
        return self.pattern.sub(self.repl, self.source)

class MakoTemplate(BaseTemplate):
    def prepare(self, **options):
        from mako.template import Template
        from mako.lookup import TemplateLookup
        options.update({'input_encoding':self.encoding})
        lookup = TemplateLookup(directories=self.lookup, **options)
        if self.source:
            self.tpl = Template(self.source, lookup=lookup, **options)
        else:
            self.tpl = Template( filename=self.filename, lookup=lookup, **options)

    def render(self, *args, **kwargs):
        for dictarg in args: kwargs.update(dictarg)
        _defaults = self.env.copy()
        _defaults.update(kwargs)
        return self.tpl.render(**_defaults)

def template(*args, **kwargs):
    if not args: raise ValueError
    template_lookup = ['.', 'templates']
    tpl = args[0]
    for cfg in args[1:]:
        if type(cfg) == dict:
            kwargs.update(cfg)
    adapter = kwargs.pop('template_adapter', BaseTemplate)
    lookup = kwargs.pop('template_lookup', template_lookup)
    settings = kwargs.pop('template_settings', {})
    if isinstance(tpl, adapter):
        temp = tpl
    elif "\n" in tpl or "{" in tpl or "%" in tpl or '$' in tpl:
        temp = adapter(source=tpl, lookup=lookup, *settings)
    else:
        temp = adapter(filename=tpl, lookup=lookup, *settings)
    return temp.render(kwargs)

mako_template = functools.partial(template, template_adapter=MakoTemplate)

class Graily:
    def __init__(self, handlers, **settings):
        self.server = None
        self.handlers = [(re.compile(r'^.*$'), NotFoundHandler)]
        self.parse_handlers(handlers)

    def parse_handlers(self, handlers):
        for _h, _f in handlers:
            self.handlers.insert(-1, (re.compile(_h), _f))

    def get_handler(self, url):
        for _h, _f in self.handlers:
            match = _h.match(url)
            if match:
                return _f, match.groups()
        return False, ()

    def server_bind(self, server_address, request_class=BaseHTTPRequestHandler):
        setattr(request_class, 'get_handler', self.get_handler)
        # self.server = HTTPServer(server_address, request_class)
        self.server = ThreadPollTCPServer(server_address, request_class)

    def serve_forever(self):
        return self.server.serve_forever()

    def wsgi(self, environ, start_response):
        # for k,v in environ.items():
        #     print(k,v)

        handler = WSGIAppHandler(environ, self.get_handler)
        try: status, headers = handler.process()
        except:
            status = "500 Internal Server Error"
            headers = [("Content-Type", "text/plain; charset=UTF-8")]
            start_response(status, headers, sys.exc_info())
            result = []
        else:
            result = handler.finish_response(start_response(status, headers))
        return result

    def __call__(self, environ, start_response):
        return self.wsgi(environ, start_response)

def errno_from_exception(err):
    if hasattr(err, 'errno'): return err.errno
    elif err.args: return err.args[0]

def _quote_html(html):
    return html.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")

def init_log(**kwargs):
    config = {'format': '%(asctime)s %(levelname)s %(message)s',
            'level': logging.INFO,
            }
    config.update(kwargs)
    logging.basicConfig(**config)

def make_server(server_address, app, server_class=WSGIServer,
        handler_class=WSGIRequestHandler):
    server = server_class(server_address, handler_class)
    server.set_app(app)
    return server
