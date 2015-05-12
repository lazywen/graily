from graily import TCPServer, StreamRequestHandler, init_log

def main():
    import logging
    init_log(level=logging.DEBUG)

    class Echo(StreamRequestHandler):
        def verify_request(self):
            # Request finished when received data ends with '.',
            # you can also define your own protocol
            return bytes([self.iostream._read_buffer[-1]]) == b'.'

        def dataReceived(self):
            self.write(self.data)

    server = TCPServer(("", 8888), Echo)
    server.serve_forever()

if __name__ == '__main__':
    main()
