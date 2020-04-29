# pyfilesystem_dokan
PyFilesystem2 expose function from PyFileystem

## About
Is the dokan expose function (fs.expose.dokan) from [PyFilesystem](https://github.com/PyFilesystem/pyfilesystem) modified to work with [PyFilesystem2](https://github.com/PyFilesystem/pyfilesystem2) with the [Dokany](https://github.com/dokan-dev/dokany) Library

This is an port of the original PyFilesystem fs.expose.dokan to work with PyFilesystem2, this requires python3, six, PyFilesystem2 and Dokan installed.

Creating, Deleting and Renaming Folders works from explorer.
Creating, Deleting, Copying, Moving/Renaming and Modifying files works too,

TODO:
- Add Documentation
- Code comments
- Clean Code
- Create and change proper module name

Please report all the problems you encounter, preferably with an step-by-step example for recreating it, it's been tested with OSFS and MemoryFS.

## Usage
Copy the dokanmount folder to your project folder
Open testdokan.py for an example, proper documentation would be added eventually, sorry for the inconvinience

