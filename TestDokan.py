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
	fs.appendtext('test.txt', 'This is a test file', encoding=u'utf-8', errors=None, newline=u'')
	fs.makedir("TestDir")
	fs.create('TestDir/subtest.txt')
	fs.appendtext('TestDir/subtest.txt', 'This is a test file in a subfolder', encoding=u'utf-8', errors=None, newline=u'')
	flags = dokan.DOKAN_OPTION_DEBUG | dokan.DOKAN_OPTION_STDERR | dokan.DOKAN_OPTION_REMOVABLE
	a = dokan.mount(fs, "Q:\\", foreground=True, numthreads=2, flags=flags)
	#fs.close()
finally:
	rmtree(path)