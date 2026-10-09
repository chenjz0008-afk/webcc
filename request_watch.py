"""Abort upstream reads when the caller disconnects or loses permission."""
import select
import socket
import threading
from contextlib import contextmanager


@contextmanager
def watch(handler, upstream):
    stopped, interrupted = threading.Event(), threading.Event()
    reason = []
    def monitor():
        while not stopped.wait(.25):
            try:
                handler.check_caller()
                if select.select([handler.connection], [], [], 0)[0] and not handler.connection.recv(1, socket.MSG_PEEK):
                    from manager import ClientGone
                    raise ClientGone()
            except Exception as error:
                reason.append(error)
                interrupted.set()
                sock = upstream.sock
                if sock:
                    try:
                        sock.shutdown(socket.SHUT_RDWR)
                    except OSError:
                        pass
                return
    worker = threading.Thread(target=monitor, daemon=True)
    worker.start()
    try:
        yield interrupted, reason
    finally:
        stopped.set()
        worker.join(timeout=1)
