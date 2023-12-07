# python

import json
import logging
import os
import pathlib
import platform
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
import urllib.parse

import pip._vendor.packaging.tags
import requests
import simplifiedapp

LOGGER = logging.getLogger(__name__)

PACKAGE_NAME_MAP = {
	'duo_client_python'	: 'duo-client',
	'service_identity'	: 'service-identity',
	'setuptools_scm'	: 'setuptools-scm',
}

def build_wheel(source_dir = '.', venv = './venv'):
	'''Build a wheel
	Get a wheel from the source tree of a module.
	'''
	
	source_dir = pathlib.Path(source_dir)
	if not isinstance(venv, VirtualEnvironmentManager):
		venv = VirtualEnvironmentManager(path = venv)
	venv.install('wheel')
	
	result = venv('setup.py', 'bdist_wheel')
	return result

def download_tarball(version_tag, destination = './', download_path_template = 'https://dl.duosecurity.com/duoauthproxy-{version_tag}-src.tgz', stream_chunk_size = 1048576):
	'''Download tarball
	Downloads the installation tarball for the specified version
	- destination: where the tarball will end up on
	- download_path_template: a "format string" which get evaluated with "version_tag = version_tag" and should yield a URL
	- stream_chunk_size: size of the stream chunks for the download
	'''
	
	download_url = download_path_template.format(version_tag = version_tag)
	local_file = pathlib.Path(destination) / pathlib.Path(urllib.parse.urlparse(download_url).path).name
	
	with requests.get(download_url, stream = True) as source_file:
		source_file.raise_for_status()
		with open(local_file, 'wb') as file_obj:
			for chunk in source_file.iter_content(chunk_size = stream_chunk_size):
				file_obj.write(chunk)
				
	return str(local_file)

def get_wheels(tarball, output_dir = './wheels', ignore_packages = ['python-'], skip_existing_wheels = True, resolve_upstream_names = True):
	'''Get wheels
	Given a tarball, detect all the packages in wheels present and build the ones that live as source trees. Extract/put all wheels in the "output_dir" 
	'''
	
	tarball = pathlib.Path(tarball)
	tarball_obj = tarfile.open(name = tarball)
	LOGGER.info('Identifying packages from tarball: %s', tarball)
	
	output_dir = pathlib.Path(output_dir)
	LOGGER.debug('Confirming output directory: %s', output_dir)
	output_dir.mkdir(parents = True, exist_ok = True)
	
	tarball_members = {pathlib.Path(tarball_member.name) : tarball_member for tarball_member in tarball_obj.getmembers()}
	tarball_root = next(iter(tarball_members)).parts[0]
	LOGGER.debug('Got tarball root (the directory within) to be: %s', tarball_root)
	
	third_party_dir = pathlib.Path(tarball_root) / 'pkgs'
	packages = set()
	for tarball_member in tarball_members:
		if third_party_dir in tarball_member.parents:
			module_name = tarball_member.relative_to(third_party_dir).parts[0]
			for ignoring_package in ignore_packages:
				if module_name[:len(ignoring_package)].lower() == ignoring_package.lower():
					module_name = None
					break
			if module_name is not None:
				packages.add(module_name)
	
	source_packages, wheels_present = {}, {}
	for package in packages:
		if package[-4:] == '.whl':
			wheels_present[package] = tarball_members[third_party_dir / package]
		else:
			source_packages[package] = [tarinfo for path, tarinfo in tarball_members.items() if third_party_dir / package in path.parents]
	
	with tempfile.TemporaryDirectory() as workdir:
		workdir = pathlib.Path(workdir)
		for wheel_name, wheel_tarinfo in wheels_present.items():
			LOGGER.debug('Extracting wheel from tarfile: %s', wheel_name)
			tarball_obj.extract(wheel_tarinfo, path = workdir)
		for wheel in (workdir / third_party_dir).iterdir(): 
			if skip_existing_wheels and (output_dir / wheel.name).exists():
				continue
			wheel = shutil.move(wheel, output_dir / wheel.name)
			LOGGER.info('Finished placing wheel: %s', wheel)
		
		with tempfile.TemporaryDirectory() as work_area:
			work_area = pathlib.Path(work_area)
			
			for source_package, source_package_content in source_packages.items():
				LOGGER.debug('Extracting source tree from tarfile: %s', source_package)
				for source_package_file in source_package_content:
					tarball_obj.extract(source_package_file, path = workdir)
				shutil.move(workdir / third_party_dir / source_package, work_area / source_package)
			
			work_venv = VirtualEnvironmentManager(path = work_area / 'venv', overwrite = True)
			work_venv.install('build', no_index = False, no_deps = False)
			if os.name == 'nt':
				work_venv.install('py2exe', no_index = False, no_deps = False)
			wheels, pypi_wheels = [], []
			for entry in output_dir.iterdir():
				if work_venv.compatible_wheel(entry.name):
					wheels.append(str(entry))
				else:
					details = VirtualEnvironmentManager.parse_wheel_name(entry.name)
					pypi_wheel = '{}=={}'.format(details['distribution'], details['version'])
					if pypi_wheel not in pypi_wheels:
						pypi_wheels.append(pypi_wheel)
			
			if wheels:
				work_venv.install(*wheels)
			if pypi_wheels:
				work_venv.install(*pypi_wheels, no_index = False)
			
			weird_structures = []
			for source_package in source_packages:
				setup_py = work_area / source_package / 'setup.py'
				if not setup_py.exists():
					weird_structures.append(source_package)
					continue
				work_venv(setup_py, 'bdist_wheel', cwd = work_area / source_package)
				for wheel in (work_area / source_package / 'dist').iterdir():
					wheel = shutil.move(wheel, output_dir / wheel.name)
					LOGGER.info('Finished placing wheel: %s', wheel)
			
			hidden_wheels = {}
			for structure in weird_structures:
				for the_dir, child_dirs, child_files in os.walk((work_area / structure)):
					for child in child_files:
						# LOGGER.warning('Looking for .whl extension: %s (%s)', child, child[-4:])
						if child[-4:] == '.whl':
							wheel = pathlib.Path(the_dir) / child
							details = work_venv.parse_wheel_name(wheel.name)
							pypi_wheel = '{}=={}'.format(details['distribution'], details['version'])
							if pypi_wheel not in hidden_wheels:
								hidden_wheels[pypi_wheel] = None
							if work_venv.compatible_wheel(wheel.name):
								hidden_wheels[pypi_wheel] = wheel

			more_pypi_wheels = []
			LOGGER.warning('Processing hidden wheels: %s', hidden_wheels)
			for pypi_entry, wheel in hidden_wheels.items():
				if wheel is None:
					more_pypi_wheels.append(pypi_entry)
				else:
					wheel = shutil.move(wheel, output_dir / wheel.name)
					LOGGER.info('Finished placing wheel: %s', wheel)
					work_venv.install(wheel)

			if more_pypi_wheels:
				LOGGER.warning('Installing incompatible hidden wheels from pypi: %s', more_pypi_wheels)
				work_venv.install(*more_pypi_wheels, no_index = False)

			work_venv('-m', 'pip', 'check')
			return work_venv.modules


class VirtualEnvironmentManager:
	'''Manage a virtual environment
	A hopefully useful class to manage your local python virtual environment using subprocess.
	'''

	WHEEL_NAMING_CONVENTION = '(?P<distribution>.+)-(?P<version>[^-]+)(?:-(?P<build_tag>[^-]+))?-(?P<python_tag>[^-]+)-(?P<abi_tag>[^-]+)-(?P<platform_tag>[^-]+)\.whl'
	
	def __call__(self, *arguments, capture_output = None, cwd = None):
		'''Run something
		Run the virtual environment's python with the provided arguments
		'''
		
		if capture_output is None:
			capture_output = self._show_output
		
		result = subprocess.run((str(self.python),) + tuple(arguments), capture_output = capture_output, cwd = cwd, check = False, text = True)
		if self._show_output:
			if result.stderr:
				print(result.stderr)
			if result.stdout:
				print(result.stdout)
		result.check_returncode()
		return result
	
	def __getattr__(self, name):
		'''Magic attribute resolution
		Lazy calculation of certain attributes
		'''
		
		if name == 'compatible_tags':
			value = {'py3-none-any', 'py38-none-any'} | {str(tag) for tag in pip._vendor.packaging.tags.cpython_tags()}
		elif name == 'python':
			value = self.path / ('Scripts' if os.name == 'nt' else 'bin') / 'python'
		else:
			raise AttributeError(name)
		
		self.__setattr__(name, value)
		return value
	
	def __init__(self, path = './venv', overwrite = False, show_output = True):
		'''Magic initialization
		Initial environment creation, re-creation, or just assume it's there.
		'''
		
		self.path = pathlib.Path(path)
		self._show_output = show_output
		
		if overwrite and self.path.exists():
			shutil.rmtree(self.path)
		
		if not self.path.exists():
			subprocess.run((sys.executable, '-m', 'venv', str(self.path)), capture_output = not self._show_output, check = True)
			self('-m', 'pip', 'install', '--upgrade', 'pip')
	
	def compatible_wheel(self, wheel):
		'''Check wheel compatibility
		Uses the platform tag from the wheel name to check if it's compatible with the current platform.
		
		Using the list from https://stackoverflow.com/questions/446209/possible-values-from-sys-platform
		'''

		details = self.parse_wheel_name(wheel)
		possible_tags = set()
		for python_tag in details['python_tag']:
			for abi_tag in details['abi_tag']:
				for platform_tag in details['platform_tag']:
					possible_tags.add('-'.join((python_tag, abi_tag, platform_tag)))
		
		return bool(possible_tags & self.compatible_tags)
	
	def install(self, *packages, upgrade = False, no_index = True, no_deps = True):
		'''Install a package
		The package can be whatever "pip install" expects.
		'''
		
		command = ['-m', 'pip', 'install']
		if upgrade:
			command.append('--upgrade')
		if no_index:
			command.append('--no-index')
		if no_deps:
			command.append('--no-deps')
		command += list(packages)
		
		return self(*command)
		
	@property
	def modules(self):
		'''List of modules
		Simple "pip list" as a python dictionary (name : version)
		'''
		
		result = self('-m', 'pip', 'list', '--format', 'json', capture_output = True)
		return {module['name'] : module['version'] for module in json.loads(result.stdout)}
	
	@classmethod
	def parse_wheel_name(cls, wheel_name):
		'''Parse wheel name
		Parse the provided name according to PEP-491
		'''
		
		result = re.match(cls.WHEEL_NAMING_CONVENTION, wheel_name)
		if result is not None:
			result = result.groupdict()
			#Because PEP-425 is a thing
			if result['python_tag']:
				result['python_tag'] = result['python_tag'].split('.')
			if result['abi_tag']:
				result['abi_tag'] = result['abi_tag'].split('.')
			if result['platform_tag']:
				result['platform_tag'] = result['platform_tag'].split('.')
			
		return result

if __name__ == '__main__':
	simplifiedapp.main()
