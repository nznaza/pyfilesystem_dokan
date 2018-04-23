# pyfilesystem_dokan
PyFilesystem2 dokan mount function from PyFileystem

This is the dokan mount function (fs.expose.dokan) from [PyFilesystem](https://github.com/PyFilesystem/pyfilesystem) modified to work with [PyFilesystem2](https://github.com/PyFilesystem/pyfilesystem) with the [Dokany](https://github.com/dokan-dev/dokany) Library

## About
This is an early port of the original PyFilesystem fs.expose.dokan to work with PyFilesystem2, you will need PyFilesystem2 and Dokan installed and python3, right now some basic functionality like copying and opening files are working

### Usage
Open pyfsdokan.py for an example, proper documentation would be added eventually, sorry for the inconvinience 

## Other information
TODO:
- Removing/Update all reference to old PyFilesystem functions for the appropiated ones
- Add Documentation
- Code comments

Please report all the problems you encounter, preferably with an steb-by-step example for recreting it, right now, I have tested OSFS and MemoryFS with open/viewing and copying working on both, and create/writing working in OSFS.

This piece of code is very early stage, took me some time to find where some functions where located or named, diferences in the parameters to take the required information, 