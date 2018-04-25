import dokan
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
	flags = dokan.DOKAN_OPTION_DEBUG | dokan.DOKAN_OPTION_STDERR | dokan.DOKAN_OPTION_REMOVABLE
	dokan.mount(fs, "Q:\\", foreground=True, numthreads=1, flags=flags)
	fs.close()
finally:
	rmtree(path)