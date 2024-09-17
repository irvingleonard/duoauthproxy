#!python
"""

"""

from simplifiedapp import main

try:
	import duoauthproxy_installer
except ModuleNotFoundError:
	import __init__ as duoauthproxy_installer

main(duoauthproxy_installer)
