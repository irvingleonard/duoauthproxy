#!python
"""A setuptools based setup module.

ToDo:
- Everything
"""

from setuptools import setup
from simplifiedapp import object_metadata

import duoauthproxy_installer

setup(**object_metadata(duoauthproxy_installer))
