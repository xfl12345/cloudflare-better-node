import io

class LimitedBytearrayIO(io.BufferedIOBase):

    """Buffered I/O implementation using an in-memory bytes buffer."""

    # Initialize _buffer as soon as possible since it's used by __del__()
    # which calls close()

    # _buffer = None

    def __init__(self, bytearray_obj:bytearray):
        # self._buffer = bytearray_obj
        self._buffer_view =  memoryview(bytearray_obj)
        self._pos = 0

    def __getstate__(self):
        if self.closed:
            raise ValueError("__getstate__ on closed file")
        return self.__dict__.copy()

    def getvalue(self):
        """Return the bytes value (contents) of the buffer
        """
        if self.closed:
            raise ValueError("getvalue on closed file")
        # return bytes(self._buffer)
        return self._buffer_view.tobytes()

    def getbuffer(self):
        """Return a readable and writable view of the buffer.
        """
        if self.closed:
            raise ValueError("getbuffer on closed file")
        # return memoryview(self._buffer)
        return self._buffer_view

    def close(self):
        # if self._buffer is not None:
        #     self._buffer.clear()
        self._buffer_view.release()
        super().close()

    def read(self, size=-1):
        if self.closed:
            raise ValueError("read from closed file")
        if size is None:
            size = -1
        else:
            try:
                size_index = size.__index__
            except AttributeError:
                raise TypeError(f"{size!r} is not an integer")
            else:
                size = size_index()
        buffer_curr_size = self._buffer_view.nbytes
        if size < 0:
            # size = len(self._buffer)
            size = buffer_curr_size
        # if len(self._buffer) <= self._pos:
        #     return b""
        if buffer_curr_size <= self._pos:
            return b""
        newpos = min(buffer_curr_size, self._pos + size)
        b = self._buffer_view[self._pos : newpos]
        self._pos = newpos
        return bytes(b)

    def read1(self, size=-1):
        """This is the same as read.
        """
        return self.read(size)

    def write(self, b):
        if self.closed:
            raise ValueError("write to closed file")
        if isinstance(b, str):
            raise TypeError("can't write str to binary stream")
        with memoryview(b) as view:
            n = view.nbytes  # Size of any bytes-like object
        if n == 0:
            return 0
        pos = self._pos
        buffer_curr_size = self._buffer_view.nbytes
        # if pos > len(self._buffer):
        if pos + n > buffer_curr_size:
            raise EOFError("input bytes is too large")
        self._buffer_view[pos:pos + n] = b
        self._pos += n
        return n

    def seek(self, pos, whence=0):
        if self.closed:
            raise ValueError("seek on closed file")
        try:
            pos_index = pos.__index__
        except AttributeError:
            raise TypeError(f"{pos!r} is not an integer")
        else:
            pos = pos_index()
        if whence == 0:
            if pos < 0:
                raise ValueError("negative seek position %r" % (pos,))
            self._pos = pos
        elif whence == 1:
            self._pos = max(0, self._pos + pos)
        elif whence == 2:
            # self._pos = max(0, len(self._buffer) + pos)
            if pos > 0:
                self._pos = self._buffer_view.nbytes
            else:
                self._pos = max(0, self._buffer_view.nbytes + pos)
        else:
            raise ValueError("unsupported whence value")
        return self._pos

    def tell(self):
        if self.closed:
            raise ValueError("tell on closed file")
        return self._pos

    # def truncate(self, pos=None):
    #     if self.closed:
    #         raise ValueError("truncate on closed file")
    #     if pos is None:
    #         pos = self._pos
    #     else:
    #         try:
    #             pos_index = pos.__index__
    #         except AttributeError:
    #             raise TypeError(f"{pos!r} is not an integer")
    #         else:
    #             pos = pos_index()
    #         if pos < 0:
    #             raise ValueError("negative truncate position %r" % (pos,))
    #     del self._buffer[pos:]
    #     return pos

    def readable(self):
        if self.closed:
            raise ValueError("I/O operation on closed file.")
        return True

    def writable(self):
        if self.closed:
            raise ValueError("I/O operation on closed file.")
        return True

    def seekable(self):
        if self.closed:
            raise ValueError("I/O operation on closed file.")
        return True
