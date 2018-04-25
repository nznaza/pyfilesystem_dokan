# pyfilesystem_dokan
PyFilesystem2 expose function from PyFileystem

## About
Is the dokan expose function (fs.expose.dokan) from [PyFilesystem](https://github.com/PyFilesystem/pyfilesystem) modified to work with [PyFilesystem2](https://github.com/PyFilesystem/pyfilesystem2) with the [Dokany](https://github.com/dokan-dev/dokany) Library

This is an port of the original PyFilesystem fs.expose.dokan to work with PyFilesystem2, you will need python3, six, PyFilesystem2 and Dokan installed, Improvements will be made, in the meantime you should be able to use Dokan for your project.

TODO:
- Port other fs.expose modules
- Fix background Functionality of dokan.mount
- Add Documentation
- Code comments

Please report all the problems you encounter, preferably with an step-by-step example for recreating it, it's been tested with OSFS and MemoryFS.

## Usage
Open testdokan.py for an example, proper documentation would be added eventually, sorry for the inconvinience 

## Other information
I will keep updating the code as soon as find more time to give to this project, and try port other fs.expose functions, but feel free to contribute reporting errors, or to the code. I ported the code as needed to open files from the MemoryFS without copying to a physical drive and therefore not everything is tested, be carefull as no warranty is granted.