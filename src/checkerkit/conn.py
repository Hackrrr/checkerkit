import logging
import socket
from typing import final


@final
class DEFAULT_TIMEOUT:
    pass


GLOBAL_DEFAULT_TIMEOUT = 3

logger = logging.getLogger(__name__)


class Conn:
    def __init__(
        self,
        host: str,
        port: int,
        default_timeout: int | None | type[DEFAULT_TIMEOUT] = DEFAULT_TIMEOUT,
        recv_size: int = 1024,
    ):
        self.host: str = host
        self.port: int = port
        self.buffer: bytearray = bytearray()
        self.recv_size: int = recv_size
        self.log: logging.Logger = logging.getLogger(
            f"{__name__}.Conn('{host}:{port}')"
        )
        self.default_timeout: int | None = (
            GLOBAL_DEFAULT_TIMEOUT
            if default_timeout is DEFAULT_TIMEOUT
            else default_timeout
        )
        self.open()

    def open(self):
        self.s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.s.connect((self.host, self.port))

    def close(self):
        self.s.close()

    def shutdown(self):
        self.s.shutdown(socket.SHUT_WR)

    def _recv(
        self, timeout: float | None | type[DEFAULT_TIMEOUT] = DEFAULT_TIMEOUT
    ) -> bytes:
        if timeout is DEFAULT_TIMEOUT:
            timeout = self.default_timeout
        if timeout is None:
            x = self.s.recv(self.recv_size)
        else:
            original = self.s.timeout
            self.s.settimeout(timeout)
            x = self.s.recv(self.recv_size)
            self.s.settimeout(original)
        self.log.debug("Received %s bytes: %r", len(x), x)
        return x

    def _recv_eof_check(
        self, timeout: float | None | type[DEFAULT_TIMEOUT] = DEFAULT_TIMEOUT
    ) -> bytes:
        x = self._recv(timeout)
        if len(x) == 0:
            raise EOFError(f"Unexpected connection EOF")
        return x

    def peek(
        self, timeout: float | None | type[DEFAULT_TIMEOUT] = DEFAULT_TIMEOUT
    ) -> bytearray:
        """Peeks current buffer.

        If current buffer is empty, receives some data first.
        Copy of buffer is returned.
        """
        if len(self.buffer) == 0:
            self.buffer += self._recv_eof_check(timeout)
        return self.buffer.copy()

    def recv(
        self,
        size: int = -1,
        timeout: float | None | type[DEFAULT_TIMEOUT] = DEFAULT_TIMEOUT,
    ) -> bytearray:
        """Receives *some* data. No gurantee about size of received data if `size` is `-1`."""
        self.log.debug("recv(%s); Current buffer: %r", size, self.buffer)
        if size == -1:
            if len(self.buffer) == 0:
                return bytearray(self._recv_eof_check(timeout))
            output = self.buffer
            self.buffer = bytearray()
            return output
        while len(self.buffer) < size:
            self.buffer += self._recv_eof_check(timeout)
        output = self.buffer[:size]
        self.buffer = self.buffer[size:]
        return output

    def recv_until(
        self,
        target: bytes,
        drop: bool = True,
        timeout: float | None | type[DEFAULT_TIMEOUT] = DEFAULT_TIMEOUT,
    ) -> bytearray:
        self.log.debug("recv_until(%r); Current buffer: %r", target, self.buffer)
        while True:
            index = self.buffer.find(target)
            if index != -1:
                output = self.buffer[0 : index + (0 if drop else len(target))]
                self.buffer = self.buffer[index + len(target) :]
                return output
            self.buffer += self._recv_eof_check(timeout)

    def recv_line(
        self,
        drop: bool = True,
        timeout: float | None | type[DEFAULT_TIMEOUT] = DEFAULT_TIMEOUT,
    ) -> bytearray:
        return self.recv_until(b"\n", drop, timeout)

    def recv_all(
        self, timeout: float | None | type[DEFAULT_TIMEOUT] = DEFAULT_TIMEOUT
    ) -> bytearray:
        self.log.debug("recv_all(); Current buffer: %r", self.buffer)
        while True:
            x = self._recv(timeout)
            if len(x) == 0:
                break
            self.buffer += x
        output = self.buffer
        self.buffer = bytearray()
        return output

    def recv_for(
        self, timeout: float | None | type[DEFAULT_TIMEOUT] = DEFAULT_TIMEOUT
    ) -> bytearray:
        # TODO: This is poorly named, it is actually something like "recv_repeatedly_until_timeout".
        # One would expect, that "recv_for" receives exactly for X seconds.
        # Also `timeout = None` doesn't makes sense in any case...
        self.log.debug("recv_for(); Current buffer: %r", self.buffer)
        while True:
            try:
                x = self._recv(timeout)
                if len(x) == 0:
                    break
                self.buffer += x
            except TimeoutError:
                break
        output = self.buffer
        self.buffer = bytearray()
        return output

    def send(self, data: bytes):
        self.log.debug("Sending %s bytes: %r", len(data), data)
        self.s.sendall(data)

    def send_line(self, data: bytes):
        self.send(data + b"\n")
