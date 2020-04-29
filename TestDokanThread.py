import dokanmount
import os.path
import tempfile
from fs.osfs import OSFS
from fs.memoryfs import MemoryFS
from shutil import rmtree
from six import b

fs = MemoryFS()
fs.create('test.txt')
fs.appendtext('test.txt', 'This is a test file', encoding=u'utf-8', errors=None, newline=u'')
fs.makedir("TestDir")
fs.create('TestDir/subtest.txt')
fs.appendtext('TestDir/subtest.txt', 'This is a test file in a subfolder', encoding=u'utf-8', errors=None, newline=u'')
#flags = dokanmount.DOKAN_OPTION_DEBUG | dokanmount.DOKAN_OPTION_STDERR | dokanmount.DOKAN_OPTION_REMOVABLE
flags = dokanmount.DOKAN_OPTION_REMOVABLE
dm = dokanmount.mount(fs, "Q:\\", foreground=False, numthreads=2, flags=flags)
print ("Memory FS is now mounted!")
input("Press any key to create file...")
fs.create('PostMountCreatedFile.txt')
fs.appendtext('PostMountCreatedFile.txt', 'This is a file was populated after Dokan mounted', encoding=u'utf-8', errors=None, newline=u'')
print("You may need to refresh folder for the file to show up")
input("Press any key to unmount drive...")
dm.unmount()