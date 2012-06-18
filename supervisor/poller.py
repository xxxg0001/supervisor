import select
import errno
import time
import signal

class BasePoller:

    def __init__(self, options):
        self.options = options
        self.initialize()

    def initialize(self):
        pass

    def register_readable(self, fd):
        raise NotImplementedError

    def register_writable(self, fd):
        raise NotImplementedError

    def unregister(self, fd):
        raise NotImplementedError

    def poll(self, timeout):
        raise NotImplementedError

class SelectPoller(BasePoller):

    def initialize(self):
        self._select = select
        self.readable = []
        self.writable = []

    def register_readable(self, fd):
        self.readable.append(fd)

    def register_writable(self, fd):
        self.writable.append(fd)

    def unregister(self, fd):
        if fd in self.readable:
            self.readable.remove(fd)
        if fd in self.writable:
            self.writable.remove(fd)

    def unregister_all(self):
        self.writable = []
        self.readable = []

    def poll(self, timeout):
        try:
            r, w, x = self._select.select(self.readable, self.writable, [], timeout)
        except select.error, err:
            if err[0] == errno.EINTR:
                self.options.logger.blather('EINTR encountered in poll')
                return [], []
            if err[0] == errno.EBADF:
                self.options.logger.blather('EBADF encountered in poll')
                self.unregister_all()
                return [], []
            raise
        return r, w

class PollPoller(BasePoller):

    def initialize(self):
        self._poller = select.poll()
        self.READ = select.POLLIN | select.POLLPRI | select.POLLHUP
        self.WRITE = select.POLLOUT

    def register_readable(self, fd):
        self._poller.register(fd, self.READ)

    def register_writable(self, fd):
        self._poller.register(fd, self.WRITE)

    def unregister(self, fd):
        self._poller.unregister(fd)

    def poll(self, timeout):
        fds = self._poll_fds(timeout)
        readables, writables = [], []
        for fd, eventmask in fds:
            if self._ignore_invalid(fd, eventmask):
                continue
            if eventmask & self.READ:
                readables.append(fd)
            if eventmask & self.WRITE:
                writables.append(fd)
        return readables, writables

    def _poll_fds(self, timeout):
        try:
            return self._poller.poll(timeout * 1000)
        except select.error, err:
            if err[0] == errno.EINTR:
                self.options.logger.blather('EINTR encountered in poll')
                return []
            raise

    def _ignore_invalid(self, fd, eventmask):
        if eventmask & select.POLLNVAL:
            # POLLNVAL means `fd` value is invalid, not open.
            # When a process quits it's `fd`s are closed so there
            # is no more reason to keep this `fd` registered
            # If the process restarts it's `fd`s are registered again
            self.unregister(fd)
            return True
        return False


class KQueuePoller(BasePoller):
    '''
    Wrapper for select.kqueue()/kevent()
    '''

    max_events = 1000

    def initialize(self):
        self._kqueue = select.kqueue()

    def register_readable(self, fd):
        kevent = select.kevent(fd, filter=select.KQ_FILTER_READ,
                               flags=select.KQ_EV_ADD)
        self._kqueue.control([kevent], 0)

    def register_writable(self, fd):
        kevent = select.kevent(fd, filter=select.KQ_FILTER_WRITE,
                               flags=select.KQ_EV_ADD)
        self._kqueue.control([kevent], 0)

    def unregister(self, fd):
        kevent = select.kevent(fd, filter=(select.KQ_FILTER_READ | select.KQ_FILTER_WRITE),
                               flags=select.KQ_EV_DELETE)
        self._kqueue.control([kevent], 0)

    def poll(self, timeout):
        readables, writables = [], []

        try:
            kevents = self._kqueue.control(None, self.max_events, timeout)
        except OSError, error:
            if error.errno == errno.EINTR:
                self.options.logger.blather('EINTR encountered in poll')
                return readables, writables
            raise

        for kevent in kevents:
            if kevent.filter == select.KQ_FILTER_READ:
                readables.append(kevent.ident)
            if kevent.filter == select.KQ_FILTER_WRITE:
                writables.append(kevent.ident)

        return readables, writables


def implements_poll():
    return hasattr(select, 'poll')

def implements_kqueue():
    return hasattr(select, 'kqueue')

if implements_kqueue():
    Poller = KQueuePoller
elif implements_poll():
    Poller = PollPoller
else:
    Poller = SelectPoller
