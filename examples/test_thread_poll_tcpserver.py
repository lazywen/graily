import time
from graily import ThreadPollTCPServer, Concurrent, StreamRequestHandler, init_log

def main():
    import logging
    init_log(level=logging.DEBUG)

    class Echo(StreamRequestHandler):
        @Concurrent.register
        def dataReceived(self):
            time.sleep(5)  # won't block the main Thread
            self.write(self.data)
    server = ThreadPollTCPServer(("", 8888), Echo)
    server.serve_forever()

if __name__ == '__main__':
    main()
