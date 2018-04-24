import errno
import sys
from functools import wraps

from fs.errors import *
from fs.path import abspath, combine, iteratepath, join, normpath


def convert_fs_errors(func):
    """Function wrapper to convert FSError instances into OSError."""
    @wraps(func)
    def wrapper(*args, **kwds):
        try:
            return func(*args, **kwds)
        except ResourceNotFound as e:
            raise OSError(errno.ENOENT, str(e))
        except ResourceNotFound as e:
            if sys.platform == "win32":
                raise OSError(errno.ESRCH, str(e))
            else:
                raise OSError(errno.ENOENT, str(e))
        except ResourceInvalid as e:
            raise OSError(errno.EINVAL, str(e))
        except PermissionDenied as e:
            raise OSError(errno.EACCES, str(e))
        except ResourceLocked as e:
            if sys.platform == "win32":
                raise WindowsError(32, str(e))
            else:
                raise OSError(errno.EACCES, str(e))
        except DirectoryNotEmpty as e:
            raise OSError(errno.ENOTEMPTY, str(e))
        except DestinationExists as e:
            raise OSError(errno.EEXIST, str(e))
        except InsufficientStorage as e:
            raise OSError(errno.ENOSPC, str(e))
        except RemoteConnectionError as e:
            raise OSError(errno.ENETDOWN, str(e))
        except Unsupported as e:
            raise OSError(errno.ENOSYS, str(e))
        except FSError as e:
            raise OSError(errno.EFAULT, str(e))
    return wrapper

class PathMap(object):
    """Dict-like object with paths for keys.

    A PathMap is like a dictionary where the keys are all FS paths.  It has
    two main advantages over a standard dictionary.  First, keys are normalized
    automatically::

        >>> pm = PathMap()
        >>> pm["hello/world"] = 42
        >>> print pm["/hello/there/../world"]
        42

    Second, various dictionary operations (e.g. listing or clearing values)
    can be efficiently performed on a subset of keys sharing some common
    prefix::

        # list all values in the map
        pm.values()

        # list all values for paths starting with "/foo/bar"
        pm.values("/foo/bar")

    Under the hood, a PathMap is a trie-like structure where each level is
    indexed by path name component.  This allows lookups to be performed in
    O(number of path components) while permitting efficient prefix-based
    operations.
    """

    def __init__(self):
        self._map = {}

    def __getitem__(self, path):
        """Get the value stored under the given path."""
        m = self._map
        for name in iteratepath(path):
            try:
                m = m[name]
            except KeyError:
                raise KeyError(path)
        try:
            return m[""]
        except KeyError:
            raise KeyError(path)

    def __contains__(self, path):
        """Check whether the given path has a value stored in the map."""
        try:
            self[path]
        except KeyError:
            return False
        else:
            return True

    def __setitem__(self, path, value):
        """Set the value stored under the given path."""
        m = self._map
        for name in iteratepath(path):
            try:
                m = m[name]
            except KeyError:
                m = m.setdefault(name, {})
        m[""] = value

    def __delitem__(self, path):
        """Delete the value stored under the given path."""
        ms = [[self._map, None]]
        for name in iteratepath(path):
            try:
                ms.append([ms[-1][0][name], None])
            except KeyError:
                raise KeyError(path)
            else:
                ms[-2][1] = name
        try:
            del ms[-1][0][""]
        except KeyError:
            raise KeyError(path)
        else:
            while len(ms) > 1 and not ms[-1][0]:
                del ms[-1]
                del ms[-1][0][ms[-1][1]]

    def get(self, path, default=None):
        """Get the value stored under the given path, or the given default."""
        try:
            return self[path]
        except KeyError:
            return default

    def pop(self, path, default=None):
        """Pop the value stored under the given path, or the given default."""
        ms = [[self._map, None]]
        for name in iteratepath(path):
            try:
                ms.append([ms[-1][0][name], None])
            except KeyError:
                return default
            else:
                ms[-2][1] = name
        try:
            val = ms[-1][0].pop("")
        except KeyError:
            val = default
        else:
            while len(ms) > 1 and not ms[-1][0]:
                del ms[-1]
                del ms[-1][0][ms[-1][1]]
        return val

    def setdefault(self, path, value):
        m = self._map
        for name in iteratepath(path):
            try:
                m = m[name]
            except KeyError:
                m = m.setdefault(name, {})
        return m.setdefault("", value)

    def clear(self, root="/"):
        """Clear all entries beginning with the given root path."""
        m = self._map
        for name in iteratepath(root):
            try:
                m = m[name]
            except KeyError:
                return
        m.clear()

    def iterkeys(self, root="/", m=None):
        """Iterate over all keys beginning with the given root path."""
        if m is None:
            m = self._map
            for name in iteratepath(root):
                try:
                    m = m[name]
                except KeyError:
                    return
        for (nm, subm) in list(m.items()):
            if not nm:
                yield abspath(root)
            else:
                k = combine(root, nm)
                for subk in self.iterkeys(k, subm):
                    yield subk

    def __iter__(self):
        return iter(list(self.keys()))

    def keys(self, root="/"):
        return list(self.iterkeys(root))

    def itervalues(self, root="/", m=None):
        """Iterate over all values whose keys begin with the given root path."""
        root = normpath(root)
        if m is None:
            m = self._map
            for name in iteratepath(root):
                try:
                    m = m[name]
                except KeyError:
                    return
        for (nm, subm) in list(m.items()):
            if not nm:
                yield subm
            else:
                k = combine(root, nm)
                for subv in self.itervalues(k, subm):
                    yield subv

    def values(self, root="/"):
        return list(self.itervalues(root))

    def iteritems(self, root="/", m=None):
        """Iterate over all (key,value) pairs beginning with the given root."""
        root = normpath(root)
        if m is None:
            m = self._map
            for name in iteratepath(root):
                try:
                    m = m[name]
                except KeyError:
                    return
        for (nm, subm) in list(m.items()):
            if not nm:
                yield (abspath(normpath(root)), subm)
            else:
                k = combine(root, nm)
                for (subk, subv) in self.iteritems(k, subm):
                    yield (subk, subv)

    def items(self, root="/"):
        return list(self.iteritems(root))

    def iternames(self, root="/"):
        """Iterate over all names beneath the given root path.

        This is basically the equivalent of listdir() for a PathMap - it yields
        the next level of name components beneath the given path.
        """
        m = self._map
        for name in iteratepath(root):
            try:
                m = m[name]
            except KeyError:
                return
        for (nm, subm) in list(m.items()):
            if nm and subm:
                yield nm

    def names(self, root="/"):
        return list(self.iternames(root))
