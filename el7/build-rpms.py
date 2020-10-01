#!python
'''duoauthproxy RPM packager
This script extracts the python modules from the duoauthproxy tarball, creates a virtual environment with the extracted wheels, and packages the environment on an RPM. You should probably run this using the python version shipped in the tarball (which should be built and packaged beforehand)
'''

import argparse
import ast
import json
import logging
import os
import pathlib
import platform
import pprint
import re
import shutil
import ssl
import subprocess
import sys
import tarfile
import tempfile
import urllib.request
import urllib.parse

__version__ = '0.2.0'

BASE_DIRECTORY = 'rpmvenv'
BASE_VENV_PACKAGES = ('wheel', 'patch', 'packaging')
DEFAULT_DIRECTORIES = {
	'build'		: 'build',
	'source'	: 'source',
	'venv'		: 'venv',
}
DEFAULT_LOG_PARAMETERS = {
	'format'	: '%(asctime)s|%(name)s|%(levelname)s:%(message)s',
	'datefmt'	: '%H:%M:%S',
}
LOGGER = logging.getLogger(__name__)
NON_MODULES = ('python', 'openssl', 'openssl-fips')
SETUPPY_TEMPLATE = '''#!python
"""A setuptools based setup module.
"""

import setuptools

metadata = {setuppy_metadata}

setuptools.setup(
	description = 'Just a dummy package for rpmvenv to work',
	long_description = "The rpmvenv package (https://github.com/kevinconway/rpmvenv) won't work only with wheels at this point. This package dependes on the wanted wheels so the environment gets built the right way.",
	url = 'https://github.com/corpitsysadmins/duoauthproxy',
	author = 'Modernizing Medicine, Inc.',
	author_email = 'corpitsysadmins@modmed.com',
#	 license='MIT',
	classifiers = [
		'Development Status :: 3 - Alpha',
	],
	**metadata
)
'''
RPMVENV_TEMPLATE = {
	'extensions': {'enabled': ['blocks', 'file_extras', 'file_permissions', 'python_venv']},
    'core': {
        'summary': 'On-premises service that receives authentication requests from your local devices and applications via RADIUS or LDAP',
        'group': 'Application/System',
        'license': 'Other',
        'url': 'https://duo.com/docs/authproxy-reference',
        'requires': [
        	'python38-altinstall-duoauthproxy',
        ],
    },
    'blocks': {
        'desc': [
            'The Duo Authentication Proxy is an on-premises software service that receives authentication requests from your local devices and applications via RADIUS or LDAP, optionally performs primary authentication against your existing LDAP directory or RADIUS authentication server, and then contacts Duo to perform secondary authentication. Once the user approves the two-factor request (received as a push notification from Duo Mobile, or as a phone call, etc.), the Duo proxy returns access approval to the requesting device or application.',
            '\n',
            'This package includes a virtual environment created out of the modules included in the upstream tarball and packaged using rpmvenv (https://github.com/kevinconway/rpmvenv).',
        ]
    },
    'file_extras': {'files': []},
    'file_permissions': {
		'user': 'root',
		'group': 'root'
	},
    'python_venv': {
    	'flags': [],
    	'pip_flags': '--no-index',
        'python': sys.executable,
    }
}
SYSTEM_FILES = ('.DS_Store',)
THIS_FILE = pathlib.Path(__file__).resolve(strict = True)


class MissingWheelError(RuntimeError):
	pass


class StandardDUOProxy:
	
	RPMVENV_PACKAGES = ('virtualenv', 'rpmvenv')
	
	def __init__(self, release_tag, *args, download_certificate = None, openssl_dist = False, recreate_paths = True, rpmbuild = 'rpmbuild', show_output = False, skip_packages = (), source_tarball = None, target_install_path = '/opt/duoauthproxy', venv_base_packages = (), **kwargs):
		'''Instance initialization
		The connection is initialized but a login is not triggered.
		'''
		
		if download_certificate is not None:
			self.download_certificate = pathlib.Path(download_certificate)
		else:
			self.download_certificate = False
		if openssl_dist:
			self.openssl_dist = pathlib.Path(openssl_dist)
		else:
			self.openssl_dist = openssl_dist
		self.recreate_paths = recreate_paths
		self.release_tag = release_tag
		self.rpmbuild = pathlib.Path(rpmbuild)
		self.tarball_url, self.tarball_path  = False, False
		if source_tarball is not None:
			if len(urllib.parse.urlparse(source_tarball).netloc):
				self.tarball_url = source_tarball
			else:
				self.tarball_path = pathlib.Path(source_tarball)
		
		self.show_output = show_output
		self.skip_packages = skip_packages
		self.target_install_path = pathlib.Path(target_install_path)
		if not self.target_install_path.is_absolute():
			raise ValueError('"--target-install-path" should be an absolute path')
		self.venv_base_packages = venv_base_packages
		
		self.rpmvenv_template = RPMVENV_TEMPLATE.copy()
		self.rpmvenv_template['core']['release'] = self.release_tag
		self.rpmvenv_template['python_venv']['name'] = self.target_install_path.name
		self.rpmvenv_template['python_venv']['path'] = self.target_install_path.parent.relative_to('/')
	
	def __call__(self, *args, max_build_passes = 10, **kwargs):
		
		if str(self.venv_python) != sys.executable:
			LOGGER.info('Re-running from within venv: %s', self.venv_path)
			sys.exit(subprocess.run([self.venv_python] + sys.argv).returncode)
		else:
			global patch
			import patch
		
		venv_modules = list(self.installed_wheels.keys())
		tarball_modules = list(self.pkg_list.keys())
		wheels = {module : self.get_wheel(module, do_not_build = True) for module in venv_modules if module in tarball_modules}
		unwheeled = [item for item in tarball_modules if item not in venv_modules + list(NON_MODULES)]
		install_queue = []
		build_passes = 0
		lp_unwheeled = ()
		lp_install_queue = ()
		
		LOGGER.debug('Hunting for wheels')
		while (len(unwheeled) or len(install_queue)) and (build_passes < max_build_passes):
			build_passes += 1
			LOGGER.debug('Processing packages. Pass %d', build_passes)

			for module in tuple(unwheeled):
		
				LOGGER.debug('Getting wheel for %s', module)
				wheel = self.get_wheel(module)
				if wheel is not None:
					wheels[module] = wheel
					unwheeled.remove(module)
					install_queue = [module] + install_queue
		
			for module in tuple(install_queue):
				try:
					LOGGER.debug('Installing module %s', module)
					self.install_wheel(module, wheels[module])
				except Exception:
					LOGGER.error('Module installation failed: %s', module)
				else:
					install_queue.remove(module)
			
			if (tuple(unwheeled) == lp_unwheeled) and (tuple(install_queue) == lp_install_queue):
				LOGGER.warning("There's no progress. It won't get better than this.")
				break
			else:
				lp_unwheeled = tuple(unwheeled)
				lp_install_queue = tuple(install_queue)
		LOGGER.debug('Hunt is over')
		
		if len(unwheeled) or len(install_queue):
			LOGGER.error('Hunting was unsuccessfull. It was not possible to build all the required modules.')
			return {
				'unwheeled'		: unwheeled,
				'install_queue'	: install_queue,
			}
		
		LOGGER.debug('Collecting wheels')
		wheels_path = self.build_path / 'wheels'
		if self.recreate_paths or not wheels_path.exists():
			reset_directory(wheels_path)
		for module, wheel in wheels.items():
			LOGGER.info('Copying module %s: %s', module, wheel)
			shutil.copy2(wheel, wheels_path)
		self.rpmvenv_template['python_venv']['pip_flags'] = ' '.join((self.rpmvenv_template['python_venv']['pip_flags'], '--find-links', str(wheels_path.relative_to(self.build_path)) + '/')) 
		
		requirements_file = self.build_path / 'requirements.txt'
		requirements_file.write_text('\n'.join(list(wheels.keys()) + ['']))
		
		setup_py_file = self.build_path / 'setup.py'
		setup_py_metadata = {
			'name' : self.name + '-rpmvenv',
			'version' : self.version,
			'install_requires' : [self.name],
		}
		setup_py_file.write_text(SETUPPY_TEMPLATE.format(setuppy_metadata = repr(setup_py_metadata)))
		
		rpmvenv_json = self.prepare_for_rpm()
		
		rpmvenv_environment = os.environ.copy()
		rpmvenv_environment['PATH'] = ':'.join((str(self.venv_python.parent), rpmvenv_environment['PATH']))
		rpmvenv_command = (self.venv_rpmvenv, '--destination', self.rpm_destination, rpmvenv_json)
		LOGGER.info('Building RPM: %s', ' '.join(map(str, rpmvenv_command)))
		return subprocess.run(tuple(map(str, rpmvenv_command)), capture_output = not self.show_output, check = True, env = rpmvenv_environment)
	
	def __getattr__(self, name):
		
		if (len(name) > 5) and (name[-5:] == '_path'):
			
			short_name = name[:-5]
			if name == 'base_path':
				value = pathlib.Path(BASE_DIRECTORY).resolve()
			else:
				value = self.base_path / DEFAULT_DIRECTORIES[short_name]
			
			preparer_name = '_prepare_{}_directory'.format(short_name)
			if hasattr(self, preparer_name):
				preparer_function = getattr(self, preparer_name)
			else:
				preparer_function = lambda value, populate = True: value
			
			if self.recreate_paths or not value.exists():
				if self.recreate_paths:
					LOGGER.warning("There's something in the %s directory. Cleaning up to deploy again: %s", short_name, value)
				else:
					LOGGER.debug('Creating %s directory: %s', short_name, value)
				reset_directory(value, create_empty = False if short_name in ('venv',) else True)
				value = preparer_function(value)
			else:
				LOGGER.debug('The %s directory is already there; using it: %s', short_name, value)
				value = preparer_function(value, populate = False)
			self.__setattr__(name, value)
			return value
		
		elif name == 'installed_wheels':
			command = [str(self.venv_python), '-m', 'pip', 'list', '--format', 'json']
			LOGGER.debug('Running: %s', ' '.join(command))
			value = subprocess.run(command, capture_output = True, check = True)
			if self.show_output:
				print(value.stderr.decode('utf8'), value.stdout.decode('utf8'), sep = '\n', end = '\n')
			value = json.loads(value.stdout)
			value = {item['name'].lower() : item['version'] for item in value}
			self.__setattr__(name, value)
			return value
			
		elif name == 'pkg_list':
			value = self._pkg_list()
			self.__setattr__(name, value)
			return value
		
		elif name == 'rpm_destination':
			value = pathlib.Path(self.rpmbuild).resolve(strict = True) / 'RPMS' / platform.machine()
			if not value.exists():
				value = THIS_FILE.parent
			self.__setattr__(name, value)
			return value
		
		elif name == 'tarball_file_obj':
			
			sources_dir = self.rpmbuild / 'SOURCES'
			if sources_dir.exists():
				tarball_file = [child for child in sources_dir.iterdir() if child.name not in SYSTEM_FILES]
				if len(tarball_file) == 1:
					return open(tarball_file[0], 'br')
				elif not len(tarball_file):
					LOGGER.debug("Couldn't find the tarball in %s", sources_dir)
				else:
					LOGGER.debug('Too many "tarball" files: %s', tarball_file)
			else:
				LOGGER.debug("The rpmbuild tree doesn't look right: %s", sources_dir)
			
			if self.tarball_path:
				if self.tarball_path.exists():
					LOGGER.debug('Using local source file %s', self.tarball_path)
					return open(self.tarball_path, mode = 'br')
				else:
					LOGGER.warning('Tarball not found on local path: %s', self.tarball_path)
			
			context = ssl.SSLContext()
			if self.download_certificate:
				cafile = self.download_certificate.resolve(strict = True)
				LOGGER.debug('Loading certificate chain from %s', cafile)
				context.load_verify_locations(cafile)
			else:
				LOGGER.warning("Certificate for download site not provided. Using system's default configuration.")
				context.load_default_certs()
			
			if not self.tarball_url:
				raise ValueError("Can't get the tarball. You should try using --rpmbuild or --source-tarball")
			source_file = tempfile.TemporaryFile()
			with urllib.request.urlopen(self.tarball_url, context = context) as remote_file:
				LOGGER.info('Downloading tarball from %s', self.tarball_url)
				shutil.copyfileobj(remote_file, source_file)
				source_file.seek(0)
			return source_file
			
		elif name == 'venv_python':
			value = self.venv_path / 'bin' / 'python'
			self.__setattr__(name, value)
			return value
			
		elif name == 'venv_rpmvenv':
			value = self.venv_path / 'bin' / 'rpmvenv'
			self.__setattr__(name, value)
			return value
		
		raise AttributeError(name)
	
	def _build_wheel(self, module):
		
		LOGGER.debug('Building %s | %s', module, self.pkg_list[module])
		
		for child in self.pkg_list[module].iterdir():
			if (child.name in ['build', 'dist']) or ((len(child.name) > 9) and (child.name[-9:] == '.egg-info')):
				shutil.rmtree(child)
		
		environment = None
		
		if module == 'setuptools':
			LOGGER.debug('Boostraping setuptools in %s', self.pkg_list[module])
			venv_result = subprocess.run([self.venv_python, 'bootstrap.py'], cwd = self.pkg_list[module], capture_output = not self.show_output, check = True)
		elif (module == 'cryptography') and self.openssl_dist:
			environment = os.environ.copy()
			environment.update({
				'CFLAGS'	: '-I{}/include'.format(self.openssl_dist),
				'LDFLAGS'	: '-L{}/lib -Wl,-z,origin'.format(self.openssl_dist),
			})
			LOGGER.debug('Using modified environment for cryptography build: %s', environment)
		
		try:
			LOGGER.debug('Building wheel for %s using setup.py bdist_wheel in %s', module, self.pkg_list[module])
			command = [str(self.venv_python), 'setup.py', 'bdist_wheel']
			LOGGER.debug('Running: %s', ' '.join(command))
			venv_result = subprocess.run(command, cwd = self.pkg_list[module], capture_output = not self.show_output, check = True, env = environment)
		except subprocess.CalledProcessError:
			LOGGER.debug('The bdist_wheel method on %s failed. Trying with pip wheel', module)
			dist_dir = self.pkg_list[module] / 'dist'
			reset_directory(dist_dir)
			command = [str(self.venv_python), '-m', 'pip', 'wheel', '--no-deps', '--no-index', '--wheel-dir', str(dist_dir), '.']
			LOGGER.debug('Running: %s', ' '.join(command))
			venv_result = subprocess.run(command, cwd = self.pkg_list[module], capture_output = not self.show_output, check = True, env = environment)
	
		return self._find_wheel(module)
	
	def _find_wheel(self, module, skip_entries = SYSTEM_FILES):
		
		dist_dir = self.pkg_list[module] / 'dist'
		if not dist_dir.exists():
			raise MissingWheelError("Dist directory missing for {}".format(module))
		childs = [child for child in dist_dir.iterdir() if child.name not in skip_entries]
		if not len(childs):
			raise MissingWheelError("No wheels present for {}".format(module))
		if len(childs) != 1:
			raise RuntimeError("Too many wheels for module {}: {}".format(module, [child.name for child in childs]))
	
		return childs[0]
	
	def _pkg_list(self, skip_entries = None, duo_client_name_fix = True, underscore_fix = True):
		
		pkgs = {}
		pkgs_path = self.source_path / 'pkgs'
		
		if skip_entries is None:
			skip_entries = [*self.skip_packages, *SYSTEM_FILES]
	
		for child in pkgs_path.iterdir():
	
			parsed = str(child.name).rpartition('-')
			if len(parsed[0]) and len(parsed[2]):
				pkgs[parsed[0].lower()] = child
			else:
				pkgs[parsed[2].lower()] = child
	
		if duo_client_name_fix:
			if 'duo_client_python' in pkgs:
				pkgs['duo-client'] = pkgs['duo_client_python']
				del pkgs['duo_client_python']
		
		if underscore_fix:
			if 'twisted_connect_proxy' in pkgs:
				pkgs['twisted-connect-proxy'] = pkgs['twisted_connect_proxy']
				del pkgs['twisted_connect_proxy']
			if 'setuptools_scm' in pkgs:
				pkgs['setuptools-scm'] = pkgs['setuptools_scm']
				del pkgs['setuptools_scm']
		
		for key in skip_entries:
			if key.lower() in pkgs:
				del pkgs[key.lower()]
	
		return pkgs
	
	def _prepare_source_directory(self, source_path, populate = True, skip_entries = None):
		
		if populate:
			tarball = tarfile.open(fileobj = self.tarball_file_obj)
			LOGGER.info('Extracting files from tarball')
			tarball.extractall(source_path)
		
			self.tarball_file_obj.close()
		
		if skip_entries is None:
			skip_entries = [*self.skip_packages, *SYSTEM_FILES]
		
		childs = [child for child in source_path.iterdir() if child.name not in skip_entries]
	
		if len(childs) != 1:
			raise RuntimeError("This scripts doesn't support the current tarball distribution. The root contains too many or too few items: {}".format([child.name for child in childs]))		
		
		LOGGER.debug('Extracting package metadata from source directory name: %s', childs[0].name)
		version = re.match('(?P<name>[^-]+)-(?P<version>[^-]+)-(?P<commit>[^-]+)-src', childs[0].name)
		for key, value in version.groupdict().items():
			if key in ('name', 'version'):
				self.rpmvenv_template['core'][key] = value
			setattr(self, key, value)
		
		source_path = childs[0]
		
		patch_file = THIS_FILE.parent / 'duoauthproxy.patch'
		LOGGER.debug('Patching the duoauthproxy module using %s', patch_file)
		patch_ = patch.fromfile(str(patch_file))
		patch_.apply(strip = 1, root = source_path)
		
		return source_path
	
	def _prepare_venv_directory(self, venv_path, populate = True):
		
		if populate:
			venv_python = venv_path / 'bin' / 'python'
			venv_build_commands = [
				(sys.executable, '-m', 'venv', str(venv_path)),
				(str(venv_python), '-m', 'pip', 'install', '--upgrade', 'pip'),
				(str(venv_python), '-m', 'pip', 'install', '--no-deps', '--upgrade', *self.venv_base_packages),
				(str(venv_python), '-m', 'pip', 'uninstall', '--yes', 'setuptools'),
			]
		
			LOGGER.info('Deploying venv in %s', venv_path)
			for command in venv_build_commands:
				LOGGER.debug('Running: %s', ' '.join(command))
				result = subprocess.run(command, capture_output = not self.show_output, check = True)
	# 			LOGGER.debug('Result: %s', result)
		
		return venv_path
	
	def get_wheel(self, module, do_not_build = False):
		
		try:
			wheel = self._find_wheel(module)
		except MissingWheelError:
			if do_not_build:
				raise
			LOGGER.debug('Building wheel for %s', module)
			try:
				wheel = self._build_wheel(module)
			except Exception as err:
				LOGGER.debug('Building of module %s failed: %s', module, err)
				wheel = None
		return wheel
	
	def install_wheel(self, module, wheel, capture_output = False):
		
		LOGGER.debug('Installing wheel for %s from %s', module, wheel.parent)
		return subprocess.run([self.venv_python, '-m', 'pip', 'install', '--no-index', '--find-links', wheel.parent, module], capture_output = not self.show_output, check = True)
	
	def prepare_for_rpm(self):
		
		source_conf = self.source_path / 'conf'
		conf_dir = self.build_path / 'conf'
		LOGGER.debug('Working on conf directory %s', conf_dir)
		if not conf_dir.exists():
			LOGGER.info('Creating conf directory %s', conf_dir)
			conf_dir.mkdir()
		for config_file in source_conf.iterdir():
			if (not config_file.is_file()) or (config_file.name in SYSTEM_FILES):
				continue
			
			relative_name = config_file.relative_to(self.source_path)
			
			if not (self.build_path / relative_name).exists():
				LOGGER.debug('Copying configuration file: %s', relative_name)
				shutil.copy2(config_file, conf_dir)
			
			self.rpmvenv_template['file_extras']['files'].append({
				'src'		: relative_name,
				'dest'		: (self.target_install_path / relative_name).relative_to('/'),
			})
		
		log_dir = self.build_path / 'log'
		LOGGER.debug('Working on the log directory %s', log_dir)
		if not log_dir.exists():
			LOGGER.info('Creating log directory %s', log_dir)
			log_dir.mkdir()
		log_file = log_dir / 'authproxy.log'
		if not log_file.exists():
			LOGGER.debug('Creating empty log file %s', log_file)
			log_file.touch()
		relative_name = log_file.relative_to(self.build_path)
		self.rpmvenv_template['file_extras']['files'].append({
			'src': relative_name,
			'dest': (self.target_install_path / relative_name).relative_to('/'),
		})
		
		run_dir = self.build_path / 'run'
		LOGGER.debug('Working on the run directory %s', run_dir)
		if not run_dir.exists():
			LOGGER.info('Creating run directory %s', run_dir)
			run_dir.mkdir()
		empty_file = run_dir / '.empty_file'
		if not empty_file.exists():
			LOGGER.debug('Creating empty file %s', empty_file)
			empty_file.touch()
		relative_name = empty_file.relative_to(self.build_path)
		self.rpmvenv_template['file_extras']['files'].append({
			'src': relative_name,
			'dest': (self.target_install_path / relative_name).relative_to('/'),
		})
		
		install_file = self.pkg_list['duoauthproxy'] / 'scripts' / 'install'
		LOGGER.debug('Parsing install script %s', install_file)
		systemd_stuff = get_vars_from_python_source('\n'.join(install_file.read_text().split('\n')[2:]), ('SYSTEMD_DUO_SCRIPT_PATH', 'INITSCRIPT_SYSTEMD_TMPL'))
		systemd_unit = self.build_path / pathlib.Path(systemd_stuff['SYSTEMD_DUO_SCRIPT_PATH']).name
		if not systemd_unit.exists():
			LOGGER.info('Writing systemd unit %s', systemd_unit)
			systemd_unit.write_text(systemd_stuff['INITSCRIPT_SYSTEMD_TMPL'].replace('%(install_dir)s', str(self.target_install_path)))
		self.rpmvenv_template['file_extras']['files'].append({
			'src': systemd_unit.name,
			'dest': pathlib.Path('etc') / 'systemd' / 'system' / systemd_unit.name,
		})
		
		LOGGER.debug('Installing packages for rpmvenv: %s', self.RPMVENV_PACKAGES)
		result = subprocess.run([self.venv_python, '-m', 'pip', 'install', '--upgrade'] + list(self.RPMVENV_PACKAGES), capture_output = not self.show_output, check = True)
		
		rpmvenv_json = self.build_path / '{name}.{release_tag}.json'.format(**vars(self))
		LOGGER.debug('Writing the json file for rpmvenv: %s', rpmvenv_json)
		rpmvenv_json.write_text(json.dumps(self.rpmvenv_template, default = str, indent=4))
		return rpmvenv_json


def reset_directory(path, create_empty = True, *args, **kwargs):

	if path.is_dir():
		LOGGER.warning('Removing existing directory: %s', path)
		result = shutil.rmtree(path)
	elif path.exists():
		LOGGER.warning('Removing non directory entry: %s', path)
		result = path.unlink()
	if create_empty:
		LOGGER.debug('Creating empty directory: %s', path)
		result = path.mkdir(*args, **kwargs)
	else:
		result = None
	return result

def get_vars_from_python_source(source, var_list):
	
	if isinstance(source, pathlib.Path):
		source = source.read_text()
		
	source = ast.parse(source)
	result = {}
	for body in source.body:
		if body.__class__ == ast.Assign:
			if len(body.targets) == 1:
				var_name = getattr(body.targets[0], "id", "")
				if var_name in var_list:
					result[var_name] = ast.literal_eval(body.value)
	
	missing_vars = [key for key in var_list if key not in result]
	if len(missing_vars):
		raise ValueError('Some variables were not found: {}'.format(missing_vars))
		
	return result
	

if __name__ == '__main__':
	
	doc_lines = __doc__.splitlines()
	parser = argparse.ArgumentParser(description = doc_lines[0], epilog = doc_lines[1], formatter_class = argparse.ArgumentDefaultsHelpFormatter)
	parser.add_argument('release_tag', help='the release tag to use for the RPM')
	parser.add_argument('--base-path', default = THIS_FILE.parent, help='the working directory. Working directories will live here')
	parser.add_argument('--download-certificate', help='the certificate to use when connecting to download the source tarball')
	parser.add_argument('--log-level', choices = ['notset', 'debug', 'info', 'warning', 'error', 'critical'], default = 'info', help = 'minimum severity of the messages to be logged')
	parser.add_argument('--max-build-passes', type=int, default = 10, help='the building process is based on iterative passes; this would be the max number of those (to avoid an infinite loop)')
	parser.add_argument('--openssl-dist', help='use a specific openssl ditribution instead of relying on the system resolution')
	parser.add_argument('--recreate-paths', action = 'store_true', default = False, help='recreate directories even if they already exist')
	parser.add_argument('--rpmbuild', default = 'rpmbuild', help='the path to the rpmbuild tree')
	parser.add_argument('--show-output', action = 'store_true', default = False, help='show the output of the commands being run')
	parser.add_argument('--skip-packages', action = 'append', default = list(NON_MODULES), help='skip the build of some package in the tarball')
	parser.add_argument('--source-tarball', help='URL or path to the source code tarball')
	parser.add_argument('--target-install-path', default = '/opt/duoauthproxy', help='the path where the resulting virtual environment will end up')
	parser.add_argument('--venv-base-packages', action='append', default = list(BASE_VENV_PACKAGES), help='packages to install in the virtual environment before the building process')
	parser.add_argument('--version', action = 'version', version = __version__)
	args = parser.parse_args()
	
	log_parameters = DEFAULT_LOG_PARAMETERS.copy()
	
	if hasattr(args, 'log_level') and len(args.log_level):
		log_parameters['level'] = args.log_level.upper()
	
	logging.basicConfig(**log_parameters)
	LOGGER.debug('Logging configured  with: %s', log_parameters)
	
	vars_args = vars(args)
	
	print(StandardDUOProxy(**vars_args)(**vars_args))
	