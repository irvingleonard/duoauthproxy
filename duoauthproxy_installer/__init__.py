#!python
"""Duo Authentication Proxy Installers
Create installers for duoauthproxy distributions.
"""

from json import dumps as json_dumps, loads as json_loads
from logging import getLogger
from os import name as os_name, walk
from pathlib import Path
from shutil import copyfileobj, move
from subprocess import PIPE, STDOUT, run
from tarfile import open as tarfile_open
from tempfile import TemporaryDirectory
from urllib.parse import urlparse

from devautotools import VirtualEnvironmentManager
from requests import get as requests_get

try:
	from jinja2 import Environment as Jinja2Environment
except ImportError:
	Jinja2Environment = None
try:
	from docker import from_env as docker_from_env
except ImportError:
	docker_from_env = None

__version__ = '0.1.0.dev0'

LOGGER = getLogger(__name__)

DEFAULT_TARGET_INSTALL_PATH = '/opt/duoauthproxy'
NON_PYTHON_MODULES = ['python-']
		

class InstallerTarball:
	"""
	
	"""
	
	NON_PYTHON_MODULES = ('python-',)
	
	def __init__(self, file_path):
		"""
		
		"""
		
		self._path = Path(file_path)
	
	def __getattr__(self, item):
		"""
		
		"""
		
		if item == 'member_paths':
			value = [Path(name) for name in self.tarball_obj.getnames()]
			if not value:
				raise RuntimeError('Empty tarball "{}"'.format(str(self._path)))
		elif item == 'packages_dir':
			value = self.root_dir / 'pkgs'
		elif item == 'root_dir':
			value = self.member_paths[0].parts[0]
			for member in self.member_paths:
				if value != member.parts[0]:
					raise ValueError('Unknown tarball structure')
			value = Path(value)
		elif item == 'tarball_obj':
			value = tarfile_open(name=self._path)
		else:
			raise AttributeError(item)
		
		self.__setattr__(item, value)
		return value
		
	def extract_path(self, path, destination, *, parents=True, exist_ok=False):
		"""
		
		"""
		
		destination = Path(destination)
		member = self.tarball_obj.getmember(str(path))
		if member.isfile():
			if destination.is_dir():
				destination = destination / Path(member.name).name
			if destination.exists() and not exist_ok:
				raise FileExistsError(str(destination))
			destination.parent.mkdir(parents=parents, exist_ok=True)
			with destination.open('wb') as dest_f:
				with self.tarball_obj.extractfile(member) as source_f:
					copyfileobj(source_f, dest_f)
			return destination
		elif member.isdir():
			raise NotImplementedError('Extract a directory')
		else:
			raise NotImplementedError("Extracting something that's not a file or directory")
	
	def extract_package(self, package_name, destination):
		"""
		
		"""
		
		return self.extract_path(self.packages_dir / package_name, destination)
	
	def get_dir_members(self, directory):
		"""
		
		"""
		
		directory = Path(directory)
		if not directory.is_relative_to(self.root_dir):
			directory = self.root_dir / directory
		
		return [member for member in self.member_paths if directory in member.parents]
		
	
	def identify_modules(self):
		"""Identify modules on the tarball
		Given a tarball, detect all the packages present and sort wheels, source modules, and "special cases" (mostly "cryptography")
		"""
		
		wheels, source_modules, special = [], [], []
		for member in self.member_paths:
			if member.parent == self.packages_dir:
				if member.suffix == '.whl':
					wheels.append(member.name)
				elif self.is_python_module(member.name) and (member / 'setup.py' in self.member_paths):
					source_modules.append(member.name)
				else:
					special.append(member.name)
		
		return wheels, source_modules, special
	
	@classmethod
	def is_python_module(cls, package_name):
		"""Is it a python module?
		Checks if the provided package is not a python module based on the hardcoded NON_PYTHON_MODULES list.
		"""
		
		for ignoring_package in cls.NON_PYTHON_MODULES:
			if package_name[:len(ignoring_package)].lower() == ignoring_package.lower():
				return False
		return True


class RPMVenvTemplate(dict):
	"""

	"""
	
	VALID_BLOCKS = ('blocks', 'core', 'extensions', 'python_venv')
	
	def __init__(self, base_dir=None, *, load_defaults=True):
		"""

		"""
		
		super().__init__()
		self._base_dir = Path.cwd() if base_dir is None else Path(base_dir)
		if load_defaults:
			self.load_defaults()
	
	def __missing__(self, key):
		"""

		"""
		
		if key not in self.VALID_BLOCKS:
			raise KeyError('"{}" is not a valid rpmvenv extension'.format(key))
		
		value = {}
		self.__setitem__(key, value)
		return value
	
	def __str__(self):
		"""

		"""
		
		return json_dumps(self, default=str, indent=4)
	
	@property
	def name(self):
		"""

		"""
		
		return self['core']['name']
	
	@property
	def version(self):
		"""

		"""
		
		return self['core']['version']
	
	@version.setter
	def version(self, new_version):
		"""

		"""
		
		self['core']['version'] = new_version
	
	@version.deleter
	def version(self):
		"""

		"""
		
		del self['core']['version']
		if not self['core']:
			del self['core']
	
	@property
	def release(self):
		"""

		"""
		
		return self['core']['release']
	
	@release.setter
	def release(self, new_release):
		"""

		"""
		
		self['core']['release'] = new_release
	
	@release.deleter
	def release(self):
		"""

		"""
		
		del self['core']['release']
		if not self['core']:
			del self['core']
	
	def add_data_file(self, name, source):
		"""

		"""
		
		self['file_extras']['files'].append({
			'src': name,
			'dest': source,
		})
	
	def load_defaults(self):
		"""

		"""
		
		default_json = Path(__file__).parent / 'data' / 'default_template.json'
		if not default_json.exists():
			raise FileNotFoundError(str(default_json))
		
		self.update(json_loads(default_json.read_text()))


class DockerfileTemplate(dict):
	"""
	
	"""
	
	DEFAULTS_FILE = Path(__file__).parent / 'data' / 'dockerfile_rpm_defaults.json'
	TAG_NAME = 'duoauthproxy_installer'
	
	def __enter__(self):
		"""
		
		"""
		
		self.dockerfile = self.root_dir / 'Dockerfile'
		self.dockerfile.write_text(str(self))
		return self.dockerfile
	
	def __exit__(self, exc_type, exc_val, exc_tb):
		"""
		
		"""
		
		LOGGER.debug('Ignoring exception in context: %s(%s) | %s', exc_type, exc_val, exc_tb)
		self.dockerfile.unlink(missing_ok=True)
		
	def __getattr__(self, item):
		"""
		
		"""
		
		if item == 'client':
			value = docker_from_env()
		else:
			raise AttributeError(item)
		
		self.__setattr__(item, value)
		return value
	
	def __init__(self, root_dir=Path.cwd(), /, **details):
		"""
		
		"""
		
		if Jinja2Environment is None:
			raise ImportError('The "jinja2" package is required by {}'.format(type(self).__name__))
		if docker_from_env is None:
			raise ImportError('The "docker" package is required by {}'.format(type(self).__name__))
		
		super().__init__(details)
		self.root_dir = Path(root_dir)
		self._template_file_name = 'dockerfile_rpm_template'
		self.load_defaults('centos-stream9')
	
	def __str__(self):
		"""
		
		"""
		
		jinja_env = Jinja2Environment()
		result = jinja_env.from_string((Path(__file__).parent / 'data' / self._template_file_name).with_suffix('.jinja').read_text())
		return result.render(self)
	
	def build(self, name_tag):
		"""
		
		"""
		
		with self as dockerfile:
			result = self.client.images.build(path=str(dockerfile.parent), tag=name_tag, rm=True, forcerm=True)
		return result
	
	def load_defaults(self, defaults_name):
		"""
		
		"""
		
		values = json_loads(self.DEFAULTS_FILE.read_text())
		self.update(values[defaults_name])
		
	def run(self, fresh_build=True, /, **run_arguments):
		"""
		
		"""
		
		if fresh_build:
			self.build(self.TAG_NAME)
		
		run_arguments_ = {
			'name': self.TAG_NAME + '_temp',
			'remove': True,
			'stderr': True,
			'stdout': True,
		}
		run_arguments_.update(run_arguments)
		return self.client.containers.run(self.TAG_NAME, **run_arguments_)
	
		
class DuoAuthProxyInstaller:
	"""
	
	"""
	
	DOWNLOAD_PATH_TEMPLATE = r'https://dl.duosecurity.com/duoauthproxy-{version_tag}-src.tgz'
	
	def __call__(self, release_tag, *, target_install_path=DEFAULT_TARGET_INSTALL_PATH):
		"""
		
		"""
		
		json_file = self.prepare_rpm_structure(release_tag, target_install_path=target_install_path)
		# return list(Path.cwd().iterdir()) + list((Path.cwd() / 'staging').iterdir())
		# return json_file
		return run(('rpmvenv', '--destination', '/root/RPMS', str(json_file)), stderr=STDOUT, stdout=PIPE, text=True, check=True).stdout
	
	def __init__(self, version_tag, *, installer_root=Path.cwd(), staging_dir_name='staging', download_dir_name='downloads', wheels_dir_name='wheels'):
		"""
		
		"""
		
		self._version_tag = version_tag
		self._installer_root = Path(installer_root)
		self._staging_dir_name = staging_dir_name
		self._download_dir_name = download_dir_name
		self._wheels_dir_name = wheels_dir_name
	
	def __getattr__(self, item):
		"""
		
		"""
		
		if item == 'download_dir':
			value = self.root_path / self._download_dir_name
			value.mkdir(parents=True, exist_ok=True)
		elif item == 'root_path':
			value = self._installer_root if self._installer_root.is_absolute() else Path.cwd() / self._installer_root
			value.mkdir(parents=True, exist_ok=True)
		elif item == 'staging_dir':
			value = self.root_path / self._staging_dir_name
			value.mkdir(parents=True, exist_ok=True)
		elif item == 'tarball':
			value = InstallerTarball(self.download_tarball())
		elif item == 'venv':
			value = VirtualEnvironmentManager(path=self.root_path / 'venv')
		elif item == 'template_venv':
			value = VirtualEnvironmentManager(path=self.root_path / 'template_venv')
		elif item == 'wheels_dir':
			value = self.root_path / self._wheels_dir_name
			value.mkdir(parents=True, exist_ok=True)
		else:
			raise AttributeError(item)
		
		self.__setattr__(item, value)
		return value
	
	def collect_wheels(self, tarball_path, build_dir, *, wheels_dir_name='wheels'):
		"""
		
		"""
		
		wheels, source_modules, special = self.identify_modules(tarball_path)
		local_wheels, missing_wheels = {}, {}
		for wheel in wheels:
			wheel_data = self.template_venv.parse_wheel_name(wheel)
			if self.template_venv.compatible_wheel(wheel):
				local_wheels[wheel_data['distribution']] = wheel
			else:
				missing_wheels[wheel_data['distribution']] = wheel_data['version']
			
		
	
	def compile_requirements(self):
		"""
		
		"""
		
		# "python_venv": {
		# 	"pip_flags": "--no-index"
		# }
		
		requirements_file = self.staging_dir / 'requirements.txt'
		self.venv.install('simplifiedapp')
		requirements_file.write_text(self.venv('freeze', program='pip').stdout)
		return requirements_file
		return self.identify_modules(tarball_path)
	
	def download_tarball(self, *, stream_chunk_size=1048576, destination_dir=None, overwrite=False):
		"""Download tarball
		Downloads the installation tarball for the specified version
		- destination: where the tarball will end up on
		- download_path_template: a "format string" which get evaluated with "version_tag = version_tag" and should yield a URL
		- stream_chunk_size: size of the stream chunks for the download
		"""
		
		download_url = self.DOWNLOAD_PATH_TEMPLATE.format(version_tag=self._version_tag)
		destination_dir = self.download_dir if destination_dir is None else Path(destination_dir)
		local_file = destination_dir / Path(urlparse(download_url).path).name
		if local_file.exists() and not overwrite:
			return local_file
		else:
			local_file.unlink(missing_ok=True)
		
		with requests_get(download_url, stream=True) as source_file:
			source_file.raise_for_status()
			with open(local_file, 'wb') as file_obj:
				for chunk in source_file.iter_content(chunk_size=stream_chunk_size):
					file_obj.write(chunk)
		
		return local_file
	
	def prepare_rpm_structure(self, release_tag, *, target_install_path='/opt/duoauthproxy'):
		"""

		"""
		
		target_install_path = Path(target_install_path)
		if not target_install_path.is_absolute():
			raise ValueError('"target-install-path" should be an absolute path')
		
		rpmvenv_data = RPMVenvTemplate()
		rpmvenv_data.version = self._version_tag
		rpmvenv_data.release = release_tag
		
		conf_content = self.tarball.get_dir_members('conf')
		if conf_content:
			conf_dir = self.staging_dir / 'conf'
			conf_dir.mkdir(exist_ok=True)
			for file_path in conf_content:
				final_file = self.tarball.extract_path(file_path, conf_dir, exist_ok=True)
				relative_name = self.relative_to_staging(final_file)
				rpmvenv_data.add_data_file(relative_name, (target_install_path / relative_name).relative_to('/'))
		
		log_dir = self.staging_dir / 'log'
		log_dir.mkdir(exist_ok=True)
		log_file = log_dir / 'authproxy.log'
		log_file.touch()
		relative_log_name = self.relative_to_staging(log_file)
		rpmvenv_data.add_data_file(relative_log_name, (target_install_path / relative_log_name).relative_to('/'))
		
		run_dir = self.staging_dir / 'run'
		run_dir.mkdir(exist_ok=True)
		run_empty_file = run_dir / '.empty_file'
		run_empty_file.touch()
		relative_run_name = self.relative_to_staging(run_empty_file)
		rpmvenv_data.add_data_file(relative_run_name, (target_install_path / relative_run_name).relative_to('/'))
		
		self.compile_requirements()
		
		rpmvenv_json_file = self.staging_dir / '{}.{}.json'.format(rpmvenv_data.name, rpmvenv_data.version)
		rpmvenv_json_file.write_text(str(rpmvenv_data))
		
		return rpmvenv_json_file
	
	def relative_to_root(self, some_path):
		"""
		
		"""
		
		some_path = Path(some_path)
		return some_path.relative_to(self.root_path)
	
	def relative_to_staging(self, some_path):
		"""

		"""
		
		some_path = Path(some_path)
		return some_path.relative_to(self.staging_dir)
	
	@classmethod
	def run_in_docker(cls, version_tag, release_tag, rpms_dir='./rpms', *, target_install_path=DEFAULT_TARGET_INSTALL_PATH):
		"""
		
		"""
		
		rpms_dir = Path(rpms_dir)
		rpms_dir = rpms_dir.absolute() if rpms_dir.is_absolute() else (Path.cwd() / rpms_dir).absolute()
		rpms_dir.mkdir(parents=True, exist_ok=True)
		
		volumes = {str(rpms_dir): {'bind': '/root/RPMS', 'mode': 'rw'}}
		return DockerfileTemplate(version_tag=version_tag, release_tag=release_tag, target_install_path=target_install_path).run(volumes=volumes).decode('utf8')
	

def build_wheel(source_dir='.', venv='./venv'):
	"""Build a wheel
	Get a wheel from the source tree of a module.
	"""
	
	source_dir = Path(source_dir)
	LOGGER.info('Building wheel for: %s', source_dir.resolve().name)
	if not isinstance(venv, VirtualEnvironmentManager):
		venv = VirtualEnvironmentManager(path=venv)
	build_requirements = ['wheel']
	if os_name == 'nt':
		build_requirements += ['py2exe']
	venv.install(*build_requirements, no_index=False, no_deps=False)
	
	venv('setup.py', 'bdist_wheel', cwd=source_dir)
	result = list((source_dir / 'dist').iterdir())
	if not result:
		raise RuntimeError('No resulting wheel')
	elif len(result) > 1:
		raise RuntimeError('Too many resulting files')
	else:
		return result[0]
	

def get_wheels(tarball, output_dir='./wheels', include_simple_wheels=False, include_random_packages=['twisted-iocpsupport']):
	"""Get wheels
	Given a tarball, detect all the packages in wheels present and build the ones that live as source trees. Extract/put all wheels in the "output_dir" 
	"""
	
	tarball = Path(tarball)
	tarball_obj = tarfile_open(name=tarball)
	
	output_dir = Path(output_dir)
	LOGGER.debug('Confirming output directory: %s', output_dir)
	output_dir.mkdir(parents=True, exist_ok=True)
	
	LOGGER.debug('Extracting content from tarball: %s', tarball)
	with TemporaryDirectory() as workdir:
		workdir = Path(workdir)
		tarball_obj.extractall(path=workdir)
		tarball_root = list(workdir.iterdir())
		if not tarball_root:
			raise ValueError('Empty tar archive')
		elif len(tarball_root) > 1:
			raise ValueError('Unknown tarball structure')
		else:
			tarball_root = tarball_root[0]
		
		LOGGER.info('Working with: %s', tarball_root.name)
		result = []
		work_venv = VirtualEnvironmentManager(path=workdir / '.venv', overwrite=True)
		work_venv.install('build', no_index=False, no_deps=False)
		
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
					wheel = move(wheel, output_dir / wheel.name)
					LOGGER.info('Finished placing wheel: %s', wheel)
					work_venv.install(wheel)
				else:
					simple_pypi_wheels.append(pypi_entry)
		
		if simple_pypi_wheels:
			work_venv.download(*simple_pypi_wheels, dest=str(output_dir))
			LOGGER.warning('Installing incompatible simple wheels from pypi: %s', simple_pypi_wheels)
			work_venv.install(*simple_pypi_wheels, no_index=False)
		
		source_wheels, weird_structures = {}, []
		for entry in (tarball_root / 'pkgs').iterdir():
			if not is_python_module(entry.name):
				continue
			if entry.is_dir():
				if not (entry / 'setup.py').is_file():
					weird_structures.append(entry)
					continue	
				wheel = build_wheel(source_dir=entry, venv=work_venv)
				details = work_venv.parse_wheel_name(wheel.name)
				pypi_wheel = '{}=={}'.format(details['distribution'], details['version'])
				if (pypi_wheel not in source_wheels) and (pypi_wheel not in simple_wheels):
					wheel = move(wheel, output_dir / wheel.name)
					LOGGER.info('Finished placing wheel: %s', wheel)
					work_venv.install(wheel)
					source_wheels[pypi_wheel] = wheel
				else:
					LOGGER.warning('Duplicated source wheel for: %s', pypi_wheel)
		
		hidden_wheels = {}
		LOGGER.info('Looking for hidden wheels in weird structures: %s', [structure.name for structure in weird_structures])
		for structure in weird_structures:
			for the_dir, child_dirs, child_files in walk(structure):
				for child in child_files:
					if child[-4:] == '.whl':
						if not is_python_module(entry.name):
							continue
						wheel = Path(the_dir) / child
						details = work_venv.parse_wheel_name(wheel.name)
						pypi_wheel = '{}=={}'.format(details['distribution'], details['version'])
						if (pypi_wheel in source_wheels) or (pypi_wheel in simple_wheels):
							LOGGER.warning('Duplicated hidden wheel for: %s', pypi_wheel)
							continue
						if (pypi_wheel not in hidden_wheels):
							hidden_wheels[pypi_wheel] = None
						if work_venv.compatible_wheel(wheel.name):
							wheel = move(wheel, output_dir / wheel.name)
							LOGGER.info('Finished placing wheel: %s', wheel)
							work_venv.install(wheel)
							hidden_wheels[pypi_wheel] = wheel
							
		hidden_pypi_wheels = [pypi_wheel for pypi_wheel, wheel in hidden_wheels.items() if wheel is None]
		if hidden_pypi_wheels:
			work_venv.download(*hidden_pypi_wheels, dest=str(output_dir))
			LOGGER.warning('Installing incompatible hidden wheels from pypi: %s', hidden_pypi_wheels)
			work_venv.install(*hidden_pypi_wheels, no_index=False)
		
		if include_random_packages:
			work_venv.download(*include_random_packages, dest=str(output_dir))
			LOGGER.warning('Installing requested random wheels from pypi: %s', include_random_packages)
			work_venv.install(*include_random_packages, no_index=False)
		
		LOGGER.info('Checking integrity of the env after all wheels got installed')
		work_venv('check', program='pip')
		
		return set(simple_wheels.keys()) | set(source_wheels.keys()) | set(hidden_wheels.keys()) | set(include_random_packages)

def is_python_module(package_name):
	"""Is it a python module?
	Checks if the provided package is not a python module based on the hardcoded NON_PYTHON_MODULES list.
	"""

	for ignoring_package in NON_PYTHON_MODULES:
		if package_name[:len(ignoring_package)].lower() == ignoring_package.lower():
			return False
	return True

def rpmvenv_json(pip_version, venv = './venv'):
	pass
