"""
fs.expose.dokan
===============

Expose an FS object to the native filesystem via Dokan.

This module provides the necessary interfaces to mount an FS object into
the local filesystem using Dokan on win32::

	http://dokan-dev.github.io/

For simple usage, the function 'mount' takes an FS object
and new device mount point or an existing empty folder
and exposes the given FS as that path::

	>>> from fs.memoryfs import MemoryFS
	>>> from fs.expose import dokan
	>>> fs = MemoryFS()
	>>> # Mount device mount point
	>>> mp = dokan.mount(fs, "Q:\\")
	>>> mp.path
	'Q:\\'
	>>> mp.unmount()
	>>> fs = MemoryFS()
	>>> # Mount in an existing empty folder.
	>>> mp = dokan.mount(fs, "C:\\test")
	>>> mp.path
	'C:\\test'
	>>> mp.unmount()

The above spawns a new background process to manage the Dokan event loop, which
can be controlled through the returned subprocess.Popen object.  To avoid
spawning a new process, set the 'foreground' option::

	>>> #  This will block until the filesystem is unmounted
	>>> dokan.mount(fs, "Q:\\", foreground=True)

Any additional options for the Dokan process can be passed as keyword arguments
to the 'mount' function.

If you require finer control over the creation of the Dokan process, you can
instantiate the MountProcess class directly.  It accepts all options available
to subprocess.Popen::

	>>> from subprocess import PIPE
	>>> mp = dokan.MountProcess(fs, "Q:\\", stderr=PIPE)
	>>> dokan_errors = mp.communicate()[1]


If you are exposing an untrusted filesystem, you may like to apply the
wrapper class Win32SafetyFS before passing it into dokan.  This will take
a number of steps to avoid suspicious operations on windows, such as
hiding autorun files.

The binding to Dokan is created via ctypes.  Due to the very stable ABI of
win32, this should work without further configuration on just about all
systems with Dokan installed.

"""
#  Copyright (c) 2009-2010, Cloud Matrix Pty. Ltd.
#  Copyright (c) 2016-2016, Adrien J. <liryna.stark@gmail.com>.
#  All rights reserved; available under the terms of the MIT License.

import ctypes
import datetime
import errno
import logging
import os
import stat as statinfo
import subprocess
import sys
import threading
import time
from collections import deque
from functools import wraps

import six
from fs.errors import FSError, ResourceInvalid, Unsupported
from fs.path import basename, combine, join, normpath, recursepath, relpath
from fs.wrapfs import WrapFS

from fs_legacy import PathMap, convert_fs_errors

try:
	import cPickle as pickle
except ImportError:
	import pickle

try:
	import dokan.libdokan
except (NotImplementedError, EnvironmentError, ImportError, NameError,):
	is_available = False
	sys.modules.pop("libdokan", None)
	libdokan = None
else:
	is_available = True
	from ctypes.wintypes import LPCWSTR, WCHAR
	kernel32 = ctypes.windll.kernel32

logger = logging.getLogger("fs.expose.dokan")

#  Options controlling the behavior of the Dokan filesystem
#  Ouput debug message
DOKAN_OPTION_DEBUG = 1
#  Ouput debug message to stderr
DOKAN_OPTION_STDERR = 2
#  Use alternate stream
DOKAN_OPTION_ALT_STREAM = 4
#  Mount drive as write-protected.
DOKAN_OPTION_WRITE_PROTECT = 8
#  Use network drive, you need to install Dokan network provider.
DOKAN_OPTION_NETWORK = 16
#  Use removable drive
DOKAN_OPTION_REMOVABLE = 32
#  Use mount manager
DOKAN_OPTION_MOUNT_MANAGER = 64
#  Mount the drive on current session only
DOKAN_OPTION_CURRENT_SESSION = 128
#  FileLock in User Mode
DOKAN_OPTION_FILELOCK_USER_MODE = 256

#  Error codes returned by DokanMain
DOKAN_SUCCESS = 0
#  General Error
DOKAN_ERROR = -1
#  Bad Drive letter
DOKAN_DRIVE_LETTER_ERROR = -2
#  Can't install driver
DOKAN_DRIVER_INSTALL_ERROR = -3
#  Driver something wrong
DOKAN_START_ERROR = -4
#  Can't assign a drive letter or mount point
DOKAN_MOUNT_ERROR = -5
#  Mount point is invalid
DOKAN_MOUNT_POINT_ERROR = -6
#  Requested an incompatible version
DOKAN_VERSION_ERROR = -7

# Misc windows constants
FILE_LIST_DIRECTORY = 0x01
FILE_SHARE_READ = 0x01
FILE_SHARE_WRITE = 0x02
FILE_FLAG_BACKUP_SEMANTICS = 0x02000000
FILE_FLAG_OVERLAPPED = 0x40000000

FILE_ATTRIBUTE_ARCHIVE = 32
FILE_ATTRIBUTE_COMPRESSED = 2048
FILE_ATTRIBUTE_DIRECTORY = 16
FILE_ATTRIBUTE_HIDDEN = 2
FILE_ATTRIBUTE_NORMAL = 128
FILE_ATTRIBUTE_OFFLINE = 4096
FILE_ATTRIBUTE_READONLY = 1
FILE_ATTRIBUTE_SYSTEM = 4
FILE_ATTRIBUTE_TEMPORARY = 4

# From winnt.h
#  The following are masks for the predefined standard access types
READ_CONTROL = 0x20000
WRITE_DAC = 0x00040000
WRITE_OWNER = 0x00080000
SYNCHRONIZE = 0x100000

STANDARD_RIGHTS_REQUIRED = 0x000f0000

STANDARD_RIGHTS_READ = READ_CONTROL
STANDARD_RIGHTS_WRITE = READ_CONTROL
STANDARD_RIGHTS_EXECUTE = READ_CONTROL

#STANDARD_RIGHTS_ALL = 0x001F0000L
#SPECIFIC_RIGHTS_ALL = 0x0000FFFFL

# Define access rights to files and directories
FILE_READ_DATA = 0x1
FILE_LIST_DIRECTORY = 0x1
FILE_WRITE_DATA = 0x2
FILE_ADD_FILE = 0x2
FILE_APPEND_DATA = 0x4
FILE_ADD_SUBDIRECTORY = 0x4
FILE_READ_EA = 0x8
FILE_WRITE_EA = 0x10
FILE_EXECUTE = 0x20
FILE_TRAVERSE = 0x20
FILE_DELETE_CHILD = 0x40
FILE_READ_ATTRIBUTES = 0x80
FILE_WRITE_ATTRIBUTES = 0x100
DELETE = 0x10000
READ_CONTROL = 0x20000
WRITE_DAC = 0x40000
WRITE_OWNER = 0x80000

FILE_GENERIC_READ = STANDARD_RIGHTS_READ | FILE_READ_DATA | FILE_READ_ATTRIBUTES | FILE_READ_EA | SYNCHRONIZE
FILE_GENERIC_WRITE = STANDARD_RIGHTS_WRITE | FILE_WRITE_DATA | FILE_WRITE_ATTRIBUTES | FILE_WRITE_EA | FILE_APPEND_DATA | SYNCHRONIZE
FILE_GENERIC_EXECUTE = STANDARD_RIGHTS_EXECUTE | FILE_EXECUTE |FILE_READ_ATTRIBUTES | SYNCHRONIZE

#/* NT Create CreateDisposition values */
FILE_SUPERSEDE = 0
FILE_OPEN = 1
FILE_CREATE = 2
FILE_OPEN_IF = 3
FILE_OVERWRITE = 4
FILE_OVERWRITE_IF = 5
#/* NT Create CreateOptions bits */
FILE_DIRECTORY_FILE = 0x00000001
FILE_WRITE_THROUGH = 0x00000002
FILE_SEQUENTIAL_ONLY = 0x00000004
FILE_NON_DIRECTORY_FILE = 0x00000040
FILE_NO_EA_KNOWLEDGE = 0x00000200
FILE_EIGHT_DOT_THREE_ONLY = 0x00000400
FILE_RANDOM_ACCESS = 0x00000800
FILE_DELETE_ON_CLOSE = 0x1000
#/* NT Create SecurityFlags bits */
SMB_SECURITY_DYNAMIC_TRACKING = 0x01
SMB_SECURITY_EFFECTIVE_ONLY	= 0x02
#/* NT Create CreateAction return values */
FILE_SUPERSEDED = 0
FILE_OPENED = 1
FILE_CREATED = 2
FILE_OVERWRITTEN = 3
FILE_EXISTS = 4
FILE_DOES_NOT_EXIST = 5

REQ_GENERIC_READ = 0x80 | 0x08 | 0x01
REQ_GENERIC_WRITE = 0x004 | 0x0100 | 0x002 | 0x0010

STATUS_SUCCESS = 0x0
STATUS_ACCESS_DENIED = 0xC0000022
STATUS_LOCK_NOT_GRANTED = 0xC0000055
STATUS_NOT_SUPPORTED = 0xC00000BB
STATUS_OBJECT_NAME_COLLISION = 0xC0000035
STATUS_DIRECTORY_NOT_EMPTY = 0xC0000101
STATUS_NOT_LOCKED = 0xC000002A
STATUS_OBJECT_NAME_NOT_FOUND = 0xC0000034
STATUS_NOT_IMPLEMENTED = 0xC0000002
STATUS_OBJECT_PATH_NOT_FOUND = 0xC000003A
STATUS_BUFFER_OVERFLOW = 0x80000005

ERROR_ALREADY_EXISTS = 183

FILE_CASE_SENSITIVE_SEARCH = 0x00000001
FILE_CASE_PRESERVED_NAMES = 0x00000002
FILE_SUPPORTS_REMOTE_STORAGE = 0x00000100
FILE_UNICODE_ON_DISK = 0x00000004
FILE_PERSISTENT_ACLS = 0x00000008

#  Some useful per-process global information
NATIVE_ENCODING = sys.getfilesystemencoding()

DATETIME_ZERO = datetime.datetime(1, 1, 1, 0, 0, 0)
DATETIME_STARTUP = datetime.datetime.utcnow()

FILETIME_UNIX_EPOCH = 116444736000000000

MinimumFileHandler = 100

# During long-running operations, Dokan requires that the DokanResetTimeout
# function be called periodically to indicate the progress is still being
# made.  Unfortunately we don't have any facility for the underlying FS
# to make these calls for us, so we have to hack around it.
#
# The idea is to use a single background thread to monitor all active Dokan
# method calls, resetting the timeout until they have completed.  Note that
# this completely undermines the point of DokanResetTimeout as it's now
# possible for a deadlock to hang the entire filesystem.

_TIMEOUT_PROTECT_THREAD = None
_TIMEOUT_PROTECT_LOCK = threading.Lock()
_TIMEOUT_PROTECT_COND = threading.Condition(_TIMEOUT_PROTECT_LOCK)
_TIMEOUT_PROTECT_QUEUE = deque()
_TIMEOUT_PROTECT_WAIT_TIME = 4 * 60
_TIMEOUT_PROTECT_RESET_TIME = 5 * 60 * 1000


def _start_timeout_protect_thread():
	"""Start the background thread used to protect dokan from timeouts.

	This function starts the background thread that monitors calls into the
	dokan API and resets their timeouts.  It's safe to call this more than
	once, only a single thread will be started.
	"""
	global _TIMEOUT_PROTECT_THREAD
	with _TIMEOUT_PROTECT_LOCK:
		if _TIMEOUT_PROTECT_THREAD is None:
			target = _run_timeout_protect_thread
			_TIMEOUT_PROTECT_THREAD = threading.Thread(target=target)
			_TIMEOUT_PROTECT_THREAD.daemon = True
			_TIMEOUT_PROTECT_THREAD.start()


def _run_timeout_protect_thread():
	while True:
		with _TIMEOUT_PROTECT_COND:
			try:
				(when, info, finished) = _TIMEOUT_PROTECT_QUEUE.popleft()
			except IndexError:
				_TIMEOUT_PROTECT_COND.wait()
				continue
		if finished:
			continue
		now = time.time()
		wait_time = max(0, _TIMEOUT_PROTECT_WAIT_TIME - now + when)
		time.sleep(wait_time)
		with _TIMEOUT_PROTECT_LOCK:
			if finished:
				continue
			libdokan.DokanResetTimeout(_TIMEOUT_PROTECT_RESET_TIME, info)
			_TIMEOUT_PROTECT_QUEUE.append((now + wait_time, info, finished))


def timeout_protect(func):
	"""Method decorator to enable timeout protection during call.

	This decorator adds an entry to the timeout protect queue before executing
	the function, and marks it as finished when the function exits.
	"""
	@wraps(func)
	def wrapper(self, *args):
		if _TIMEOUT_PROTECT_THREAD is None:
			_start_timeout_protect_thread()
		info = args[-1]
		finished = []
		try:
			with _TIMEOUT_PROTECT_COND:
				_TIMEOUT_PROTECT_QUEUE.append((time.time(), info, finished))
				_TIMEOUT_PROTECT_COND.notify()
			return func(self, *args)
		finally:
			with _TIMEOUT_PROTECT_LOCK:
				finished.append(True)
	return wrapper


def handle_fs_errors(function):
	"""Method decorator to report FS errors in the appropriate way.

	This decorator catches all FS errors and translates them into an
	equivalent OSError, then returns the negated error number.  It also
	makes the function return zero instead of None as an indication of
	successful execution.
	"""
	function = convert_fs_errors(function)

	@wraps(function)
	def wrapper(*args, **kwds):
		try:
			response = function(*args, **kwds)
		except OSError as e:
			if e.errno:
				response = _errno2syserrcode(e.errno)
			else:
				response = STATUS_ACCESS_DENIED
		except Exception as e:
			raise
		else:
			if response is None:
				response = 0
		return response
	return wrapper


class FSOperations(object):
	"""Object delegating all DOKAN_OPERATIONS pointers to an FS object."""

	def __init__(self, fs, fsname="NTFS", volname="Dokan Volume", securityfolder=os.path.expanduser('~')):
		if libdokan is None:
			msg = 'dokan library (http://dokan-dev.github.io/) is not available'
			raise OSError(msg)
		self.fs = fs
		self.fsname = fsname
		self.volname = volname
		self.securityfolder = securityfolder
		self._files_by_handle = {}
		self._files_lock = threading.Lock()
		self._next_handle = MinimumFileHandler
		#  Windows requires us to implement a kind of "lazy deletion", where
		#  a handle is marked for deletion but this is not actually done
		#  until the handle is closed.  This set monitors pending deletes.
		self._pending_delete = set()
		#  Since pyfilesystem has no locking API, we manage file locks
		#  in memory.  This maps paths to a list of current locks.
		self._active_locks = PathMap()
		#  Dokan expects a succesful write() to be reflected in the file's
		#  reported size, but the FS might buffer writes and prevent this.
		#  We explicitly keep track of the size Dokan expects a file to be.
		#  This dict is indexed by path, then file handle.
		self._files_size_written = PathMap()

	def get_ops_struct(self):
		"""Get a DOKAN_OPERATIONS struct mapping to our methods."""
		struct = libdokan.DOKAN_OPERATIONS()
		for (nm, typ) in libdokan.DOKAN_OPERATIONS._fields_:
			setattr(struct, nm, typ(getattr(self, nm)))
		return struct

	def _get_file(self, FileHandle):
		"""Get the information associated with the given file handle."""
		try:
			return self._files_by_handle[FileHandle]
		except KeyError:
			raise FSError("invalid file handle")

	def _reg_file(self, File, FileName):
		"""Register a new file handle for the given file and path."""
		self._files_lock.acquire()
		try:
			FileHandle = self._next_handle
			self._next_handle += 1
			lock = threading.Lock()
			self._files_by_handle[FileHandle] = (File, FileName, lock)
			if FileName not in self._files_size_written:
				self._files_size_written[FileName] = {}
			self._files_size_written[FileName][FileHandle] = 0
			return FileHandle
		finally:
			self._files_lock.release()

	def _rereg_file(self, FileHandle, File):
		"""Re-register the file handle for the given file.

		This might be necessary if we are required to write to a file
		after its handle was closed (e.g. to complete an async write).
		"""
		self._files_lock.acquire()
		try:
			(f2, path, lock) = self._files_by_handle[FileHandle]
			assert f2.closed
			self._files_by_handle[FileHandle] = (File, path, lock)
			return FileHandle
		finally:
			self._files_lock.release()

	def _del_file(self, FileHandle):
		"""Unregister the given file handle."""
		self._files_lock.acquire()
		try:
			#(f, path, lock) = self._files_by_handle.pop(fh)
			path = self._files_by_handle.pop(FileHandle)[1]
			del self._files_size_written[path][FileHandle]
			if not self._files_size_written[path]:
				del self._files_size_written[path]
		finally:
			self._files_lock.release()

	def _is_pending_delete(self, FileName):
		"""Check if the given path is pending deletion.

		This is true if the path or any of its parents have been marked
		as pending deletion, false otherwise.
		"""
		for ppath in recursepath(FileName):
			if ppath in self._pending_delete:
				return True
		return False

	def _check_lock(self, FileName, Offset, length, DokanFileInfo, locks=None):
		"""Check whether the given file range is locked.

		This method implements basic lock checking.  It checks all the locks
		held against the given file, and if any overlap the given byte range
		then it returns STATUS_LOCK_NOT_GRANTED.  If the range is not locked, it will
		return zero.
		"""
		if locks is None:
			with self._files_lock:
				try:
					locks = self._active_locks[FileName]
				except KeyError:
					return STATUS_SUCCESS
		for (lh, lstart, lend) in locks:
			if DokanFileInfo is not None and DokanFileInfo.contents.Context == lh:
				continue
			if lstart >= Offset + length:
				continue
			if lend <= Offset:
				continue
			return STATUS_LOCK_NOT_GRANTED
		return STATUS_SUCCESS

	@timeout_protect
	@handle_fs_errors
	def ZwCreateFile(self, FileName, SecurityContext, DesiredAccess, FileAttributes, ShareAccess, CreateDisposition, CreateOptions, DokanFileInfo):
		FileName = self._dokanpath2pyfs(FileName)
		#  Can't open files that are pending delete.
		if self._is_pending_delete(FileName):
			return STATUS_ACCESS_DENIED

		if DesiredAccess & (FILE_READ_DATA | FILE_WRITE_DATA | FILE_APPEND_DATA | FILE_EXECUTE) == 0:
			if CreateDisposition == FILE_OPEN or CreateDisposition == FILE_CREATE:
				# From https://docs.microsoft.com/en-us/windows-hardware/drivers/ddi/content/wdm/nf-wdm-zwcreatefile
				# Do not specify FILE_READ_DATA, FILE_WRITE_DATA, FILE_APPEND_DATA, or FILE_EXECUTE when you create or open a directory.
				# If they are not defined we are opening or creating a Directory
				DokanFileInfo.contents.IsDirectory = 0

		retcode = STATUS_SUCCESS
		if self.fs.isdir(FileName) or DokanFileInfo.contents.IsDirectory == 1:
			DokanFileInfo.contents.IsDirectory = 1
			if CreateDisposition == FILE_OPEN:
				if self.fs.exists(FileName):
					return STATUS_SUCCESS
				else:
					return FILE_DOES_NOT_EXIST

			if CreateDisposition == FILE_CREATE:
				if self.fs.makedir(FileName):
					return STATUS_SUCCESS
				return FILE_DOES_NOT_EXIST

			elif CreateDisposition == FILE_OPEN_IF:
				if self.fs.exists(FileName):
					return STATUS_SUCCESS
				else:
					if self.fs.makedir(FileName):
						return STATUS_SUCCESS
					return FILE_DOES_NOT_EXIST
		else:
			#retcode =  STATUS_SUCCESS
			if DesiredAccess == 0:
				# DesiredAccess shold not be zero
				return FILE_DOES_NOT_EXIST
			if CreateDisposition == FILE_OPEN:
				mode = "r+b"
				if not self.fs.exists(FileName):
					return FILE_DOES_NOT_EXIST
			elif CreateDisposition == FILE_CREATE:
				mode = "w+b"
				if self.fs.exists(FileName):
					return ERROR_ALREADY_EXISTS
			elif CreateDisposition == FILE_OVERWRITE:
				mode = "w+b"
				if not self.fs.exists(FileName):
					return FILE_DOES_NOT_EXIST
			elif CreateDisposition == FILE_OVERWRITE_IF:
				mode = "w+b"
				retcode = FILE_OVERWRITTEN
			elif CreateDisposition == FILE_SUPERSEDE:
				mode = "w+b"
				retcode = FILE_SUPERSEDED
			elif CreateDisposition == FILE_OPEN_IF:
				mode = "w+b"
			else:
				mode = "r+b"

			try:
				f = self.fs.open(FileName, mode)
				#  print(path, mode, repr(f))
			except FSError:
					# print(e)
					raise
			else:
				DokanFileInfo.contents.Context = self._reg_file(f, FileName)
			if retcode == STATUS_SUCCESS and (CreateOptions & FILE_DELETE_ON_CLOSE):
				self._pending_delete.add(FileName)
		return retcode

	@timeout_protect
	@handle_fs_errors
	def Cleanup(self, FileName, DokanFileInfo):
		FileName = self._dokanpath2pyfs(FileName)
		if DokanFileInfo.contents.IsDirectory:
			if DokanFileInfo.contents.DeleteOnClose:
				self.fs.removedir(FileName)
				self._pending_delete.remove(FileName)
		else:
			(file, _, lock) = self._get_file(DokanFileInfo.contents.Context)
			lock.acquire()
			try:
				file.close()
				if DokanFileInfo.contents.DeleteOnClose:
					self.fs.remove(FileName)
					self._pending_delete.remove(FileName)
					self._del_file(DokanFileInfo.contents.Context)
					DokanFileInfo.contents.Context = 0
			finally:
				lock.release()

	@timeout_protect
	@handle_fs_errors
	def CloseFile(self, FileName, DokanFileInfo):
		if DokanFileInfo.contents.Context >= MinimumFileHandler:
			(file, _, lock) = self._get_file(DokanFileInfo.contents.Context)
			lock.acquire()
			try:
				file.close()
				self._del_file(DokanFileInfo.contents.Context)
			finally:
				lock.release()
			DokanFileInfo.contents.Context = 0

	@timeout_protect
	@handle_fs_errors
	def ReadFile(self, FileName, Buffer, BufferLength, ReadLength, Offset, DokanFileInfo):
		FileName = self._dokanpath2pyfs(FileName)
		(file, _, lock) = self._get_file(DokanFileInfo.contents.Context)
		lock.acquire()
		try:
			file_lock_status = self._check_lock(FileName, Offset, BufferLength, DokanFileInfo)
			if file_lock_status:
				return file_lock_status
			#  This may be called after Cleanup, meaning we
			#  need to re-open the file.
			if file.closed:
				file = self.fs.open(FileName, file.mode)
				self._rereg_file(DokanFileInfo.contents.Context, file)
			file.seek(Offset)
			data = file.read(BufferLength)
			ctypes.memmove(Buffer, ctypes.create_string_buffer(data), len(data))
			ReadLength[0] = len(data)
		finally:
			lock.release()
		return STATUS_SUCCESS

	@timeout_protect
	@handle_fs_errors
	def WriteFile(self, FileName, Buffer, NumberOfBytesToWrite, NumberOfBytesWritten, Offset, DokanFileInfo):
		FileName = self._dokanpath2pyfs(FileName)
		fh = DokanFileInfo.contents.Context
		(file, _, lock) = self._get_file(fh)
		lock.acquire()
		try:
			file_lock_status = self._check_lock(FileName, Offset, NumberOfBytesToWrite, DokanFileInfo)
			if file_lock_status !=0:
				return file_lock_status
			#  This may be called after Cleanup, meaning we
			#  need to re-open the file.
			if file.closed:
				print('reopenWriteFile')
				file = self.fs.open(FileName, file.mode)
				self._rereg_file(DokanFileInfo.contents.Context, file)
			if DokanFileInfo.contents.WriteToEndOfFile:
				file.seek(0, os.SEEK_END)
			else:
				file.seek(Offset)
			data = ctypes.create_string_buffer(NumberOfBytesToWrite)
			ctypes.memmove(data, Buffer, NumberOfBytesToWrite)
			file.write(data.raw)
			NumberOfBytesWritten[0] = len(data.raw)
			try:
				size_written = self._files_size_written[FileName][fh]
			except KeyError:
				pass
			else:
				if Offset + NumberOfBytesWritten[0] > size_written:
					new_size_written = Offset + NumberOfBytesWritten[0]
					self._files_size_written[FileName][fh] = new_size_written
		finally:
			lock.release()
		return STATUS_SUCCESS

	@timeout_protect
	@handle_fs_errors
	def FlushFileBuffers(self, FileName, DokanFileInfo):
		FileName = self._dokanpath2pyfs(FileName)
		(file, _, lock) = self._get_file(DokanFileInfo.contents.Context)
		lock.acquire()
		try:
			file.flush()
		finally:
			lock.release()
		return STATUS_SUCCESS

	@timeout_protect
	@handle_fs_errors
	def GetFileInformation(self, FileName, Buffer, DokanFileInfo):
		FileName = self._dokanpath2pyfs(FileName)
		finfo = self.fs.getinfo(FileName,namespaces=['basic','details'])
		data = Buffer.contents
		self._info2finddataw(FileName, finfo, data, DokanFileInfo)
		try:
			written_size = max(self._files_size_written[FileName].values())
		except KeyError:
			pass
		else:
			reported_size = (data.nFileSizeHigh << 32) + data.nFileSizeLow
			if written_size > reported_size:
				data.nFileSizeHigh = written_size >> 32
				data.nFileSizeLow = written_size & 0xffffffff
		data.nNumberOfLinks = 1
		return STATUS_SUCCESS

	@timeout_protect
	@handle_fs_errors
	def FindFiles(self, FileName, FillFindData, DokanFileInfo):
		FileName = self._dokanpath2pyfs(FileName)
		for (nm, finfo) in self.fs.listdirinfo(FileName):
			fpath = combine(FileName, nm)
			if self._is_pending_delete(fpath):
				continue
			data = self._info2finddataw(fpath, finfo)
			FillFindData(ctypes.byref(data), DokanFileInfo)
		return STATUS_SUCCESS

	@timeout_protect
	@handle_fs_errors
	def FindFilesWithPattern(self, FileName, SearchPattern, FillFindData, DokanFileInfo):
		FileName = self._dokanpath2pyfs(FileName)
		for nm in self.fs.listdir(FileName):
			fpath = combine(FileName, nm)
			finfo = self.fs.getinfo(fpath, namespaces=['basic','details'])
			if self._is_pending_delete(fpath):
				continue
			if not libdokan.DokanIsNameInExpression(SearchPattern, nm, True):
				continue
			data = self._info2finddataw(fpath, finfo, None)
			FillFindData(ctypes.byref(data), DokanFileInfo)

	@timeout_protect
	@handle_fs_errors
	def SetFileAttributes(self, FileName, FileAttributes, DokanFileInfo):
		FileName = self._dokanpath2pyfs(FileName)
		# TODO: decode various file attributes
		return STATUS_SUCCESS

	@timeout_protect
	@handle_fs_errors
	def SetFileTime(self, FileName, CreationTime, LastAccessTime, LastWriteTime, DokanFileInfo):
		FileName = self._dokanpath2pyfs(FileName)
		# setting ctime is not supported
		if LastAccessTime is not None:
			try:
				LastAccessTime = _filetime2datetime(LastAccessTime.contents)
			except ValueError:
				LastAccessTime = None
		if LastWriteTime is not None:
			try:
				LastWriteTime = _filetime2datetime(LastWriteTime.contents)
			except ValueError:
				LastWriteTime = None
		#  some programs demand this succeed; fake it
		try:
			self.fs.settimes(FileName, LastAccessTime, LastWriteTime)
		except Unsupported:
			pass
		return STATUS_SUCCESS

	@timeout_protect
	@handle_fs_errors
	def DeleteFile(self, FileName, DokanFileInfo):
		FileName = self._dokanpath2pyfs(FileName)
		if not self.fs.isfile(FileName):
			if not self.fs.exists(FileName):
				return STATUS_ACCESS_DENIED
			else:
				return STATUS_OBJECT_NAME_NOT_FOUND
		self._pending_delete.add(FileName)
		# the actual delete takes place in self.CloseFile()
		return STATUS_SUCCESS

	@timeout_protect
	@handle_fs_errors
	def DeleteDirectory(self, FileName, DokanFileInfo):
		FileName = self._dokanpath2pyfs(FileName)
		for nm in self.fs.listdir(FileName):
			if not self._is_pending_delete(join(FileName, nm)):
				return STATUS_DIRECTORY_NOT_EMPTY
		self._pending_delete.add(FileName)
		# the actual delete takes place in self.CloseFile()
		return STATUS_SUCCESS

	@timeout_protect
	@handle_fs_errors
	def MoveFile(self, FileName, NewFileName, overwrite, DokanFileInfo):
		#  Close the file if we have an open handle to it.
		if DokanFileInfo.contents.Context >= MinimumFileHandler:
			(file, _, lock) = self._get_file(DokanFileInfo.contents.Context)
			lock.acquire()
			try:
				file.close()
				self._del_file(DokanFileInfo.contents.Context)
			finally:
				lock.release()
		FileName = self._dokanpath2pyfs(FileName)
		NewFileName = self._dokanpath2pyfs(NewFileName)
		if DokanFileInfo.contents.IsDirectory:
			self.fs.movedir(FileName, NewFileName, create=True)
		else:
			self.fs.move(FileName, NewFileName, overwrite=True)
		return STATUS_SUCCESS

	@timeout_protect
	@handle_fs_errors
	def SetEndOfFile(self, FileName, AllocSize, DokanFileInfo):
		self._dokanpath2pyfs(FileName)
		(file, _, lock) = self._get_file(DokanFileInfo.contents.Context)
		lock.acquire()
		try:
			pos = file.tell()
			if AllocSize != pos:
				file.seek(AllocSize)
			file.truncate()
			if pos < AllocSize:
				file.seek(min(pos, AllocSize))
		finally:
			lock.release()
		return STATUS_SUCCESS

	@handle_fs_errors
	def GetDiskFreeSpace(self, FreeBytesAvailable, TotalNumberOfBytes, TotalNumberOfFreeBytes, DokanFileInfo):
		#  This returns a stupidly large number if not info is available.
		#  It's better to pretend an operation is possible and have it fail
		#  than to pretend an operation will fail when it's actually possible.
		large_amount = 100 * 1024 * 1024 * 1024
		#nBytesFree[0] = self.fs.getmeta("free_space", (large_amount))
		#nBytesTotal[0] = self.fs.getmeta("total_space", (2 * large_amount))
		TotalNumberOfFreeBytes[0] = (large_amount)
		TotalNumberOfBytes[0] = (2 * large_amount) #self.fs.getsize()
		FreeBytesAvailable[0] = TotalNumberOfFreeBytes[0]
		return STATUS_SUCCESS

	@handle_fs_errors
	def GetVolumeInformation(self, VolumeNameBuffer, VolumeNameSize, VolumeSerialNumber, MaximumComponentLenght, FileSystemFlags, FileSystemNameBuffer, FileSystemNameSize, DokanFileInfo):
		nm = ctypes.create_unicode_buffer(self.volname[:VolumeNameSize - 1])
		sz = (len(nm.value) + 1) * ctypes.sizeof(ctypes.c_wchar)
		ctypes.memmove(VolumeNameBuffer, nm, sz)
		if VolumeSerialNumber:
			VolumeSerialNumber[0] = 0
		if MaximumComponentLenght:
			MaximumComponentLenght[0] = 255
		if FileSystemFlags:
			FileSystemFlags[0] = FILE_CASE_SENSITIVE_SEARCH | FILE_CASE_PRESERVED_NAMES | FILE_SUPPORTS_REMOTE_STORAGE | FILE_UNICODE_ON_DISK | FILE_PERSISTENT_ACLS
		nm = ctypes.create_unicode_buffer(self.fsname[:FileSystemNameSize - 1])
		sz = (len(nm.value) + 1) * ctypes.sizeof(ctypes.c_wchar)
		ctypes.memmove(FileSystemNameBuffer, nm, sz)
		return STATUS_SUCCESS

	@timeout_protect
	@handle_fs_errors
	def SetAllocationSize(self, FileName, AllocSize, DokanFileInfo):
		#  I think this is supposed to reserve space for the file
		#  but *not* actually move the end-of-file marker.
		#  No way to do that in pyfs.
		return STATUS_SUCCESS

	@timeout_protect
	@handle_fs_errors
	def LockFile(self, FileName, ByteOffset, Lenght, DokanFileInfo):
		end = ByteOffset + Lenght
		with self._files_lock:
			try:
				locks = self._active_locks[FileName]
			except KeyError:
				locks = self._active_locks[FileName] = []
			else:
				status = self._check_lock(FileName, ByteOffset, Lenght, None, locks)
				if status:
					return status
			locks.append((DokanFileInfo.contents.Context, ByteOffset, end))
			return STATUS_SUCCESS

	@timeout_protect
	@handle_fs_errors
	def UnlockFile(self, FileName, ByteOffset, Lenght, DokanFileInfo):
		with self._files_lock:
			try:
				locks = self._active_locks[FileName]
			except KeyError:
				return STATUS_NOT_LOCKED
			todel = []
			for i, (lh, lstart, lend) in enumerate(locks):
				if DokanFileInfo.contents.Context == lh:
					if lstart == ByteOffset:
						if lend == ByteOffset + Lenght:
							todel.append(i)
			if not todel:
				return STATUS_NOT_LOCKED
			for i in reversed(todel):
				del locks[i]
			return STATUS_SUCCESS

	@handle_fs_errors
	def GetFileSecurity(self, FileName, SecurityInformation, SecurityDescriptor, BufferLenght, LenghtNeeded, DokanFileInfo):
		SecurityDescriptor = ctypes.cast(SecurityDescriptor, libdokan.PSECURITY_DESCRIPTOR)
		FileName = self._dokanpath2pyfs(FileName)
		if self.fs.isdir(FileName):
			res = libdokan.GetFileSecurity(
				self.securityfolder,
				ctypes.cast(SecurityInformation, libdokan.PSECURITY_INFORMATION)[0],
				SecurityDescriptor,
				BufferLenght,
				LenghtNeeded,
			)
			return STATUS_SUCCESS if res else STATUS_BUFFER_OVERFLOW
		return STATUS_NOT_IMPLEMENTED

	@handle_fs_errors
	def SetFileSecurity(self, FileName, SecurityInformation, SecurityDescriptor, BufferLenght, LenghtNeeded, DokanFileInfo):
		return STATUS_NOT_IMPLEMENTED

	@handle_fs_errors
	def Mounted(self, DokanFileInfo):
		return STATUS_SUCCESS

	@handle_fs_errors
	def Unmounted(self, DokanFileInfo):
		return STATUS_SUCCESS

	@handle_fs_errors
	def FindStreams(self, FileName, FillFindStreamData, DokanFileInfo):
		return STATUS_NOT_IMPLEMENTED

	def _dokanpath2pyfs(self, FileName):
		FileName = FileName.replace('\\', '/')
		return normpath(FileName)

	def _info2attrmask(self, FileName, DokanFileInfo, hinfo=None):
		"""Convert a file/directory info dict to a win32 file attribute mask."""
		attrs = 0
		st_mode = DokanFileInfo.get("st_mode", None)
		if st_mode:
			if statinfo.S_ISDIR(st_mode):
				attrs |= FILE_ATTRIBUTE_DIRECTORY
			elif statinfo.S_ISREG(st_mode):
				attrs |= FILE_ATTRIBUTE_NORMAL
		if not attrs and hinfo:
			if hinfo.contents.IsDirectory:
				attrs |= FILE_ATTRIBUTE_DIRECTORY
			else:
				attrs |= FILE_ATTRIBUTE_NORMAL
		if not attrs:
			if self.fs.isdir(FileName):
				attrs |= FILE_ATTRIBUTE_DIRECTORY
			else:
				attrs |= FILE_ATTRIBUTE_NORMAL
		return attrs

	def _info2finddataw(self, FileName, DokanFileInfo, data=None, hinfo=None):
		"""Convert a file/directory info dict into a WIN32_FIND_DATAW struct."""
		if data is None:
			data = libdokan.WIN32_FIND_DATAW()
		data.dwFileAttributes = self._info2attrmask(FileName, DokanFileInfo, hinfo)
		data.ftCreationTime = _datetime2filetime(DokanFileInfo.get('details',"created", None))
		data.ftLastAccessTime = _datetime2filetime(DokanFileInfo.get('details',"accessed", None))
		data.ftLastWriteTime = _datetime2filetime(DokanFileInfo.get('details',"modified", None))
		data.nFileSizeHigh = DokanFileInfo.get('details',"size", 0) >> 32
		data.nFileSizeLow = DokanFileInfo.get('details',"size", 0) & 0xffffffff
		data.cFileName = basename(FileName)
		data.cAlternateFileName = ""
		return data


def _timestamp2datetime(tstamp):
	"""Convert a unix timestamp to a datetime object."""
	return datetime.datetime.fromtimestamp(tstamp)


def _timestamp2filetime(TimeStamp):
	f = FILETIME_UNIX_EPOCH + int(TimeStamp * 10000000)
	return libdokan.FILETIME(f & 0xffffffff, f >> 32)


def _filetime2timestamp(FileTime):
	f = FileTime.dwLowDateTime | (FileTime.dwHighDateTime << 32)
	return (f - FILETIME_UNIX_EPOCH) / 10000000.0


def _filetime2datetime(FileTime):
	"""Convert a FILETIME struct info datetime.datetime object."""
	if FileTime is None:
		return DATETIME_ZERO
	if FileTime.dwLowDateTime == 0 and FileTime.dwHighDateTime == 0:
		return DATETIME_ZERO
	return _timestamp2datetime(_filetime2timestamp(FileTime))


def _datetime2filetime(DateTime):
	"""Convert a FILETIME struct info datetime.datetime object."""
	if DateTime is None:
		return libdokan.FILETIME(0, 0)
	if DateTime == DATETIME_ZERO:
		return libdokan.FILETIME(0, 0)
	return _timestamp2filetime(DateTime)


def _errno2syserrcode(eno):
	"""Convert an errno into a win32 system error code."""
	if eno == errno.EEXIST:
		return STATUS_OBJECT_NAME_COLLISION
	if eno == errno.ENOTEMPTY:
		return STATUS_DIRECTORY_NOT_EMPTY
	if eno == errno.ENOSYS:
		return STATUS_NOT_SUPPORTED
	if eno == errno.EACCES:
		return STATUS_ACCESS_DENIED
	return eno


def _check_path_string(FileName):  # TODO Probably os.path has a better check for this...
	"""Check path string."""
	if not FileName or not FileName[0].isalpha() or not FileName[1:3] == ':\\':
		raise ValueError("invalid path: %r" % (FileName,))


def mount(fs, path, foreground=False, ready_callback=None, unmount_callback=None, **kwds):
	"""Mount the given FS at the given path, using Dokan.

	By default, this function spawns a new background process to manage the
	Dokan event loop.  The return value in this case is an instance of the
	'MountProcess' class, a subprocess.Popen subclass.

	If the keyword argument 'foreground' is given, we instead run the Dokan
	main loop in the current process.  In this case the function will block
	until the filesystem is unmounted, then return None.

	If the keyword argument 'ready_callback' is provided, it will be called
	when the filesystem has been mounted and is ready for use.  Any additional
	keyword arguments control the behavior of the final dokan mount point.
	Some interesting options include:

					* numthreads:  number of threads to use for handling Dokan requests
					* fsname:  name to display in explorer etc
					* flags:   DOKAN_OPTIONS bitmask
					* securityfolder:  folder path used to duplicate security rights on all folders
					* FSOperationsClass:  custom FSOperations subclass to use

	"""
	if libdokan is None:
		raise OSError("the dokan library is not available")
	_check_path_string(path)
	#  This function captures the logic of checking whether the Dokan mount
	#  is up and running.  Unfortunately I can't find a way to get this
	#  via a callback in the Dokan API.  Instead we just check for the path
	#  in a loop, polling the mount proc to make sure it hasn't died.

	def check_alive(mp):
		if mp and mp.poll() is not None:
			raise OSError("dokan mount process exited prematurely")

	def check_ready(mp=None):
		if ready_callback is not False:
			check_alive(mp)
			for _ in  six.moves.range(100):
				try:
					os.stat(path)
				except EnvironmentError:
					check_alive(mp)
					time.sleep(0.05)
				else:
					check_alive(mp)
					if ready_callback:
						return ready_callback()
					else:
						return None
			else:
				check_alive(mp)
				raise OSError("dokan mount process seems to be hung")
	#  Running the the foreground is the final endpoint for the mount
	#  operation, it's where we call DokanMain().
	if foreground:
		numthreads = kwds.pop("numthreads", 0)
		flags = kwds.pop("flags", 0)
		FSOperationsClass = kwds.pop("FSOperationsClass", FSOperations)
		opts = libdokan.DOKAN_OPTIONS(
			libdokan.DOKAN_MINIMUM_COMPATIBLE_VERSION, numthreads, flags, 0, path, "", 2000, 512, 512)
		ops = FSOperationsClass(fs, **kwds)
		if ready_callback:
			check_thread = threading.Thread(target=check_ready)
			check_thread.daemon = True
			check_thread.start()
		opstruct = ops.get_ops_struct()
		res = libdokan.DokanMain(ctypes.byref(opts), ctypes.byref(opstruct))
		if res != DOKAN_SUCCESS:
			raise OSError("Dokan failed with error: " + str(res))
		if unmount_callback:
			unmount_callback()
	#  Running the background, spawn a subprocess and wait for it
	#  to be ready before returning.
	else:
		mp = MountProcess(fs, path, kwds)
		check_ready(mp)
		if unmount_callback:
			orig_unmount = mp.unmount

			def new_unmount():
				orig_unmount()
				unmount_callback()
			mp.unmount = new_unmount
		return mp


def unmount(path):
	"""Unmount the given path.

	This function unmounts the dokan path mounted at the given path.
	It works but may leave dangling processes; its better to use the "unmount"
	method on the MountProcess class if you have one.
	"""
	_check_path_string(path)
	if not libdokan.DokanRemoveMountPoint(path):
		raise OSError("filesystem could not be unmounted: %s" % (path,))


class MountProcess(subprocess.Popen):
	"""subprocess.Popen subclass managing a Dokan mount.

	This is a subclass of subprocess.Popen, designed for easy management of
	a Dokan mount in a background process.  Rather than specifying the command
	to execute, pass in the FS object to be mounted, the target path
	and a dictionary of options for the Dokan process.

	In order to be passed successfully to the new process, the FS object
	must be pickleable. Since win32 has no fork() this restriction is not
	likely to be lifted (see also the "multiprocessing" module)

	This class has an extra attribute 'path' giving the path of the mounted
	filesystem, and an extra method 'unmount' that will cleanly unmount it
	and terminate the process.
	"""

	#  This works by spawning a new python interpreter and passing it the
	#  pickled (fs,path,opts) tuple on the command-line.  Something like this:
	#
	#    python -c "import MountProcess; MountProcess._do_mount('..data..')
	#

	unmount_timeout = 5

	def __init__(self, fs, path, dokan_opts={}, nowait=False, **kwds):
		if libdokan is None:
			raise OSError("the dokan library is not available")
		_check_path_string(path)
		self.path = path
		cmd = "try: import cPickle as pickle;\n"
		cmd = cmd + "except ImportError: import pickle;\n"
		cmd = cmd + "data = pickle.loads(%s); "
		cmd = cmd + "from fs.expose.dokan import MountProcess; "
		cmd = cmd + "MountProcess._do_mount(data)"
		cmd = cmd % (repr(pickle.dumps((fs, path, dokan_opts, nowait), -1)),)
		cmd = [sys.executable, "-c", cmd]
		super(MountProcess, self).__init__(cmd, **kwds)

	def unmount(self):
		"""Cleanly unmount the Dokan filesystem, terminating this subprocess."""
		if not libdokan.DokanRemoveMountPoint(self.path):
			raise OSError("the filesystem could not be unmounted: %s" %(self.path,))
		self.terminate()

	if not hasattr(subprocess.Popen, "terminate"):
		def terminate(self):
			"""Gracefully terminate the subprocess."""
			kernel32.TerminateProcess(int(self), -1)

	if not hasattr(subprocess.Popen, "kill"):
		def kill(self):
			"""Forcibly terminate the subprocess."""
			kernel32.TerminateProcess(int(self), -1)

	@staticmethod
	def _do_mount(data):
		"""Perform the specified mount."""
		(fs, path, opts, nowait) = data
		opts["foreground"] = True

		def unmount_callback():
			fs.close()
		opts["unmount_callback"] = unmount_callback
		if nowait:
			opts["ready_callback"] = False
		mount(fs, path, **opts)


class Win32SafetyFS(WrapFS):
	"""FS wrapper for extra safety when mounting on win32.

	This wrapper class provides some safety features when mounting untrusted
	filesystems on win32.  Specifically:

					* hiding autorun files
					* removing colons from paths

	"""

	def __init__(self, wrapped_fs, allow_autorun=False):
		self.allow_autorun = allow_autorun
		super(Win32SafetyFS, self).__init__(wrapped_fs)

	def _encode(self, path):
		path = relpath(normpath(path))
		path = path.replace(":", "__colon__")
		if not self.allow_autorun:
			if path.lower().startswith("_autorun."):
				path = path[1:]
		return path

	def _decode(self, path):
		path = relpath(normpath(path))
		path = path.replace("__colon__", ":")
		if not self.allow_autorun:
			if path.lower().startswith("autorun."):
				path = "_" + path
		return path


if __name__ == "__main__":
	import os.path
	import tempfile
	from fs.osfs import OSFS
	from fs.memoryfs import MemoryFS
	from shutil import rmtree
	from six import b
	path = tempfile.mkdtemp()
	try:
		#fs = OSFS(path)
		fs = MemoryFS()
		fs.create('test.txt')
		fs.appendtext('test.txt', 'this is a test', encoding=u'utf-8', errors=None, newline=u'')
		flags = DOKAN_OPTION_DEBUG | DOKAN_OPTION_STDERR | DOKAN_OPTION_REMOVABLE
		mount(fs, "Q:\\", foreground=True, numthreads=1, flags=flags)
		fs.close()
	finally:
		rmtree(path)
