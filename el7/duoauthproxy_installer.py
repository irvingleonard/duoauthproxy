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

NON_PYTHON_MODULES = ['python-']

def build_wheel(source_dir = '.', venv = './venv'):
	'''Build a wheel
	Get a wheel from the source tree of a module.
	'''
	
	source_dir = pathlib.Path(source_dir)
	LOGGER.info('Building wheel for: %s', source_dir.resolve().name)
	if not isinstance(venv, VirtualEnvironmentManager):
		venv = VirtualEnvironmentManager(path = venv)
	build_requirements = ['wheel']
	if os.name == 'nt':
		build_requirements += ['py2exe']
	venv.install(*build_requirements, no_index = False, no_deps = False)
	
	venv('setup.py', 'bdist_wheel', cwd = source_dir)
	result = list((source_dir / 'dist').iterdir())
	if not result:
		raise RuntimeError('No resulting wheel')
	elif len(result) > 1:
		raise RuntimeError('Too many resulting files')
	else:
		return result[0]

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

def get_wheels(tarball, output_dir = './wheels', include_simple_wheels = False, include_random_packages = ['twisted-iocpsupport']):
	'''Get wheels
	Given a tarball, detect all the packages in wheels present and build the ones that live as source trees. Extract/put all wheels in the "output_dir" 
	'''
	
	tarball = pathlib.Path(tarball)
	tarball_obj = tarfile.open(name = tarball)
	
	output_dir = pathlib.Path(output_dir)
	LOGGER.debug('Confirming output directory: %s', output_dir)
	output_dir.mkdir(parents = True, exist_ok = True)
	
	
	LOGGER.debug('Extracting content from tarball: %s', tarball)
	with tempfile.TemporaryDirectory() as workdir:
		workdir = pathlib.Path(workdir)
		tarball_obj.extractall(path = workdir)
		tarball_root = list(workdir.iterdir())
		if not tarball_root:
			raise ValueError('Empty tar archive')
		elif len(tarball_root) > 1:
			raise ValueError('Unknown tarball structure')
		else:
			tarball_root = tarball_root[0]
		
		LOGGER.info('Working with: %s', tarball_root.name)
		result = []
		work_venv = VirtualEnvironmentManager(path = workdir / '.venv', overwrite = True)
		work_venv.install('build', no_index = False, no_deps = False)
		
		simple_wheels = {}
		for entry in (tarball_root / 'pkgs').iterdir():
			if not is_python_module(entry.name):
				continue
			if entry.suffix == '.whl':
				details = work_venv.parse_wheel_name(entry.name)
				pypi_wheel = '{}=={}'.format(details['distribution'], details['version'])
				if pypi_wheel not in simple_wheels:
					simple_wheels[pypi_wheel] = None
				if work_venv.compatible_wheel(entry.name):
					simple_wheels[pypi_wheel] = entry
		
		simple_pypi_wheels = []
		LOGGER.info('Processing simple wheels: %s', list(simple_wheels.keys()))
		for pypi_entry, wheel in simple_wheels.items():
			if wheel is None:
				simple_pypi_wheels.append(pypi_entry)
			else:
				if include_simple_wheels:
					wheel = shutil.move(wheel, output_dir / wheel.name)
					LOGGER.info('Finished placing wheel: %s', wheel)
					work_venv.install(wheel)
				else:
					simple_pypi_wheels.append(pypi_entry)
		
		if simple_pypi_wheels:
			work_venv.download(*simple_pypi_wheels, dest = str(output_dir))
			LOGGER.warning('Installing incompatible simple wheels from pypi: %s', simple_pypi_wheels)
			work_venv.install(*simple_pypi_wheels, no_index = False)
		
		source_wheels, weird_structures = {}, []
		for entry in (tarball_root / 'pkgs').iterdir():
			if not is_python_module(entry.name):
				continue
			if entry.is_dir():
				if not (entry / 'setup.py').is_file():
					weird_structures.append(entry)
					continue	
				wheel = build_wheel(source_dir = entry, venv = work_venv)
				details = work_venv.parse_wheel_name(wheel.name)
				pypi_wheel = '{}=={}'.format(details['distribution'], details['version'])
				if (pypi_wheel not in source_wheels) and (pypi_wheel not in simple_wheels):
					wheel = shutil.move(wheel, output_dir / wheel.name)
					LOGGER.info('Finished placing wheel: %s', wheel)
					work_venv.install(wheel)
					source_wheels[pypi_wheel] = wheel
				else:
					LOGGER.warning('Duplicated source wheel for: %s', pypi_wheel)
		
		hidden_wheels = {}
		LOGGER.info('Looking for hidden wheels in weird structures: %s', [structure.name for structure in weird_structures])
		for structure in weird_structures:
			for the_dir, child_dirs, child_files in os.walk(structure):
				for child in child_files:
					if child[-4:] == '.whl':
						if not is_python_module(entry.name):
							continue
						wheel = pathlib.Path(the_dir) / child
						details = work_venv.parse_wheel_name(wheel.name)
						pypi_wheel = '{}=={}'.format(details['distribution'], details['version'])
						if (pypi_wheel in source_wheels) or (pypi_wheel in simple_wheels):
							LOGGER.warning('Duplicated hidden wheel for: %s', pypi_wheel)
							continue
						if (pypi_wheel not in hidden_wheels):
							hidden_wheels[pypi_wheel] = None
						if work_venv.compatible_wheel(wheel.name):
							wheel = shutil.move(wheel, output_dir / wheel.name)
							LOGGER.info('Finished placing wheel: %s', wheel)
							work_venv.install(wheel)
							hidden_wheels[pypi_wheel] = wheel
							
		hidden_pypi_wheels = [pypi_wheel for pypi_wheel, wheel in hidden_wheels.items() if wheel is None]
		if hidden_pypi_wheels:
			work_venv.download(*hidden_pypi_wheels, dest = str(output_dir))
			LOGGER.warning('Installing incompatible hidden wheels from pypi: %s', hidden_pypi_wheels)
			work_venv.install(*hidden_pypi_wheels, no_index = False)
		
		if include_random_packages:
			work_venv.download(*include_random_packages, dest = str(output_dir))
			LOGGER.warning('Installing requested random wheels from pypi: %s', include_random_packages)
			work_venv.install(*include_random_packages, no_index = False)
		
		LOGGER.info('Checking integrity of the env after all wheels got installed')
		work_venv('check', program = 'pip')
		
		return set(simple_wheels.keys()) | set(source_wheels.keys()) | set(hidden_wheels.keys()) | set(include_random_packages)

def is_python_module(package_name):
	'''Is it a python module?
	Checks if the provided package is not a python module based on the hardcoded NON_PYTHON_MODULES list.
	'''

	for ignoring_package in NON_PYTHON_MODULES:
		if package_name[:len(ignoring_package)].lower() == ignoring_package.lower():
			return False
	return True

def rpmvenv_json(pip_version, venv = './venv'):
	pass


class VirtualEnvironmentManager:
	'''Manage a virtual environment
	A hopefully useful class to manage your local python virtual environment using subprocess.
	'''

	WHEEL_NAMING_CONVENTION = '(?P<distribution>.+)-(?P<version>[^-]+)(?:-(?P<build_tag>[^-]+))?-(?P<python_tag>[^-]+)-(?P<abi_tag>[^-]+)-(?P<platform_tag>[^-]+)\.whl'
	
	def __call__(self, *arguments, cwd = None, program = 'python'):
		'''Run something
		Run the virtual environment's python with the provided arguments
		'''
		
		if not hasattr(self, program):
			raise ValueError('Unsupported program: {}'.format(program))
		result = subprocess.run((str(getattr(self, program)),) + tuple(arguments), capture_output = True, cwd = cwd, check = False, text = True)
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
		
		if name == 'bin_scripts':
			value = self.path / ('Scripts' if os.name == 'nt' else 'bin')
		elif name == 'compatible_tags':
			value = {'py3-none-any', 'py38-none-any'} | {str(tag) for tag in pip._vendor.packaging.tags.cpython_tags()}
		elif name == 'pip':
			value = self.bin_scripts / 'pip'
		elif name == 'python':
			value = self.bin_scripts / 'python'
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
	
	def download(self, *packages, dest = '.', no_deps = True):
		'''Install a package
		The package can be whatever "pip install" expects.
		'''

		command = ['download', '--dest', dest]
		if no_deps:
			command.append('--no-deps')
		command += list(packages)

		return self(*command, program = 'pip')

	def install(self, *packages, upgrade = False, no_index = True, no_deps = True):
		'''Install a package
		The package can be whatever "pip install" expects.
		'''
		
		command = ['install']
		if upgrade:
			command.append('--upgrade')
		if no_index:
			command.append('--no-index')
		if no_deps:
			command.append('--no-deps')
		command += list(packages)
		
		return self(*command, program = 'pip')
		
	@property
	def modules(self):
		'''List of modules
		Simple "pip list" as a python dictionary (name : version)
		'''
		
		result = self('list', '--format', 'json', program = 'pip')
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
