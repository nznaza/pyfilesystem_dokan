# pyfilesystem_dokan
PyFilesystem2 dokan expose function from PyFileystem

## About
Is the dokan expose function (fs.expose.dokan) from [PyFilesystem](https://github.com/PyFilesystem/pyfilesystem) modified to work with [PyFilesystem2](https://github.com/PyFilesystem/pyfilesystem2) with the [Dokany](https://github.com/dokan-dev/dokany) Library

This is an early port of the original PyFilesystem fs.expose.dokan to work with PyFilesystem2, you will need python3, six, PyFilesystem2 and Dokan installed.

TODO:
- Removing/Update all reference to old PyFilesystem functions for the appropiated ones from PyFilesystem2
- Add Documentation
- Code comments

Please report all the problems you encounter, preferably with an steb-by-step example for recreating it, it's been tested with OSFS and MemoryFS with open/viewing and copying files working on both, and create/writing working in OSFS.

## Usage
Open pyfsdokan.py for an example, proper documentation would be added eventually, sorry for the inconvinience 

## Other information
I will keep updating the code as soon as find more time to give to this project, and try port other fs.expose functions, but feel free to contribute reporting errors, or to the code. I ported the code as needed to open files from the MemoryFS without copying to a physical drive and therefore not everything is tested, be carefull as no warranty is granted.