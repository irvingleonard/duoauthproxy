#!python
"""Duo Authentication Proxy Installers
Create installers for duoauthproxy distributions.
"""

from atexit import register as atexit_register
from json import dumps as json_dumps, loads as json_loads
from logging import getLogger
from pathlib import Path, PurePath
from shutil import copytree, copyfileobj, move, rmtree
from subprocess import PIPE, STDOUT, run
from tarfile import open as tarfile_open
from tempfile import TemporaryDirectory, mkdtemp
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
	
	BASIC_PYTHON_MODULES = ('pip', 'setuptools', 'setuptools_scm', 'wheel')
	NON_PYTHON_MODULES = ('python-',)
	SYSTEMD_UNIT_FILE_NAME = 'duoauthproxy.service'
	
	def __init__(self, file_path):
		"""
		
		"""
		
		self._path = Path(file_path)
	
	def __getattr__(self, item):
		"""
		
		"""
		
		if item == 'members':
			value = self.tarball_obj.getmembers()
			if not value:
				raise RuntimeError('Empty tarball "{}"'.format(str(self._path)))
		elif item == 'member_paths':
			value = {PurePath(member.name): member for member in self.members}
		elif item == 'packages_dir':
			value = self.root_dir / 'pkgs'
		elif item == 'python_version':
			value = None
			LOOKING_FOR = 'python-'
			for member in self.member_paths:
				if member.parent == self.packages_dir:
					if member.name[:len(LOOKING_FOR)].lower() == LOOKING_FOR.lower():
						value = member.name[len(LOOKING_FOR):].split('.')
						break
			if value is None:
				raise RuntimeError("Couldn't detect the version of the Python package")
		elif item == 'root_dir':
			value = PurePath(self.members[0].name).parts[0]
			for member in self.member_paths:
				if value != member.parts[0]:
					raise ValueError('Unknown tarball structure')
			value = PurePath(value)
		elif item == 'tarball_obj':
			value = tarfile_open(name=self._path)
		else:
			raise AttributeError(item)
		
		self.__setattr__(item, value)
		return value
	
	def build_sources(self, *source_modules, wheels_dir, venv_wheels={}):
		"""

		"""
		
		result = []
		with VirtualEnvironmentManager(path=None, show_output=False) as venv:
			venv('-m', 'pip', 'install', '--upgrade', *['=='.join(item) for item in venv_wheels.items()])
			with TemporaryDirectory() as temp_dir_name:
				temp_dir = Path(temp_dir_name)
				for module in source_modules:
					module_dir = temp_dir / module
					self.extract_package(module, temp_dir)
					try:
						venv('setup.py', 'bdist_wheel', cwd=module_dir)
					except Exception:
						LOGGER.exception("Couldn't build module: %s", module)
					else:
						module_dist = list((module_dir / 'dist').iterdir())
						if not module_dist:
							raise RuntimeError('No resulting wheel')
						elif len(module_dist) > 1:
							raise RuntimeError('Too many resulting files')
						else:
							move(module_dist[0], wheels_dir)
							result.append(wheels_dir / module_dist[0].name)
		return result
		
	def extract_file(self, path, destination=None, *, parents=True, exist_ok=False, fail_silently=False):
		"""
		
		"""
		
		path = Path(path)
		member = self.member_paths[path]
		if not member.isfile():
			if fail_silently:
				return None
			else:
				raise ValueError('Path "{}" is not a file'.format(member.name))
		
		if destination is None:
			with self.tarball_obj.extractfile(member) as source_f:
				return source_f.read()
		
		destination = Path(destination)
		if destination.is_dir():
			destination = destination / Path(member.name).name
		if destination.exists() and not exist_ok:
			raise FileExistsError(str(destination))
		destination.parent.mkdir(parents=parents, exist_ok=True)
		with destination.open('wb') as dest_f:
			with self.tarball_obj.extractfile(member) as source_f:
				copyfileobj(source_f, dest_f)
		return destination
	
	def extract_package(self, package_name, destination):
		"""
		
		"""
		
		destination = Path(destination)
		package_path = self.packages_dir / package_name
		members = [member for member in self.member_paths if package_path in member.parents]
		if members:
			result = []
			for member in members:
				extracted_member = self.extract_file(member, destination/ package_name / member.relative_to(package_path), fail_silently=True)
				if extracted_member is not None:
					result.append(extracted_member)
		else:
			result = self.extract_file(package_path, destination)
		return result
	
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
	
	def prepare_assets(self, output_dir=Path.cwd(), service_uid='root', clean_output_first=False, wheels_dir_name='wheels'):
		"""

		"""
		
		result = {}
		
		output_dir = Path(output_dir).absolute()
		if clean_output_first and output_dir.exists():
			rmtree(output_dir)
		output_dir.mkdir(parents=True, exist_ok=True)
		
		conf_content = self.get_dir_members('conf')
		if conf_content:
			result['conf'] = []
			conf_dir = output_dir / 'conf'
			conf_dir.mkdir(exist_ok=True)
			for file_path in conf_content:
				result['conf'].append(self.extract_file(file_path, conf_dir, exist_ok=True))
		
		doc_content = self.get_dir_members('doc')
		if doc_content:
			result['licenses'] = []
			licenses_dir = output_dir / 'licenses'
			licenses_dir.mkdir(exist_ok=True)
			for file_path in doc_content:
				result['licenses'].append(self.extract_file(file_path, licenses_dir, exist_ok=True))
		
		selinux_content = self.get_dir_members('selinux_policy')
		if selinux_content:
			result['selinux_policy'] = []
			selinux_dir = output_dir / 'selinux_policy'
			selinux_dir.mkdir(exist_ok=True)
			for file_path in selinux_content:
				result['selinux_policy'].append(self.extract_file(file_path, selinux_dir, exist_ok=True))
		
		extra_py_content = [member for member in self.get_dir_members(self.root_dir) if member.parent == self.root_dir]
		if extra_py_content:
			result['extra_py'] = []
			extra_py_dir = output_dir / 'extra_py'
			extra_py_dir.mkdir(exist_ok=True)
			for file_path in extra_py_content:
				if file_path.suffix == '.py':
					result['extra_py'].append(self.extract_file(file_path, extra_py_dir, exist_ok=True))
		
		wheels, source_modules, special = self.identify_modules()
		
		venv_wheels, local_wheels, result['missing_wheels'] ={}, {}, {}
		with VirtualEnvironmentManager(path=None, show_output=False) as venv:
			for wheel in wheels:
				wheel_data = venv.parse_wheel_name(wheel)
				if wheel_data['distribution'] in self.BASIC_PYTHON_MODULES:
					venv_wheels[wheel_data['distribution']] = wheel_data['version']
				elif venv.compatible_wheel(wheel):
					local_wheels[wheel_data['distribution']] = wheel
					if wheel_data['distribution'] in result['missing_wheels']:
						del result['missing_wheels'][wheel_data['distribution']]
				elif wheel_data['distribution'] not in local_wheels:
					result['missing_wheels'][wheel_data['distribution']] = wheel_data['version']
		
		result['wheels_dir'] = (output_dir / wheels_dir_name).absolute()
		
		if local_wheels:
			result['wheels_dir'].mkdir(parents=True, exist_ok=True)
			result['local_wheels'] = []
			for wheel in local_wheels.values():
				result['local_wheels'].append(self.extract_package(wheel, result['wheels_dir']))
		
		if source_modules:
			result['wheels_dir'].mkdir(parents=True, exist_ok=True)
			result['built_wheels'] = self.build_sources(*source_modules, wheels_dir=result['wheels_dir'], venv_wheels=venv_wheels)
		
		systemd_unit_template = Path(__file__).parent / 'data' / (self.SYSTEMD_UNIT_FILE_NAME + '.jinja')
		jinja_env = Jinja2Environment()
		systemd_unit = jinja_env.from_string(systemd_unit_template.read_text())
		result['systemd_unit'] = output_dir / self.SYSTEMD_UNIT_FILE_NAME
		result['systemd_unit'].write_text(systemd_unit.render({'output_dir': output_dir, 'service_uid': service_uid}))
		
		return result
		

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
	
	def __call__(self, release_tag, *, target_install_path=DEFAULT_TARGET_INSTALL_PATH, dist_dir='dist'):
		"""
		
		"""
		
		return self.build_rpm(release_tag=release_tag, target_install_path=target_install_path, rpms_dir=dist_dir)
		
	def __init__(self, version_tag, *, installer_root=Path.cwd(), download_dir_name='downloads', wheels_dir_name='wheels'):
		"""
		
		"""
		
		self._version_tag = version_tag
		self._installer_root = Path(installer_root)
		self._download_dir_name = download_dir_name
		self._wheels_dir_name = wheels_dir_name
	
	def __getattr__(self, item):
		"""
		
		"""
		
		if item == 'assets_dir':
			value = Path(mkdtemp()).absolute()
			atexit_register(rmtree, value, ignore_errors=True)
		elif item == 'download_dir':
			value = self.root_path / self._download_dir_name
			value.mkdir(parents=True, exist_ok=True)
		elif item == 'root_path':
			value = self._installer_root if self._installer_root.is_absolute() else Path.cwd() / self._installer_root
			value.mkdir(parents=True, exist_ok=True)
		elif item == 'requirements':
			with VirtualEnvironmentManager(path=None, show_output=False) as venv:
				venv.install(*[str(wheel) for wheel in self.wheels_dir.iterdir() if wheel.suffix == '.whl'], no_index=True)
				value = venv('freeze', program='pip').stdout
		elif item == 'tarball':
			value = InstallerTarball(self.download_tarball())
		elif item == 'tarball_assets':
			value = self.tarball.prepare_assets(output_dir=self.assets_dir)
		elif item == 'wheels_dir':
			value = self.root_path / self._wheels_dir_name
			copytree(self.tarball_assets['wheels_dir'], value, dirs_exist_ok=True)
			if self.tarball_assets['missing_wheels']:
				with VirtualEnvironmentManager(path=None, show_output=False) as venv:
					venv.download(*['=='.join(item) for item in self.tarball_assets['missing_wheels'].items()], dest=str(value))
		else:
			raise AttributeError(item)
		
		self.__setattr__(item, value)
		return value
	
	def build_rpm(self, release_tag, *, target_install_path=DEFAULT_TARGET_INSTALL_PATH, rpms_dir='RPMS', staging_dir='rpm_data'):
		"""

		"""
		
		target_install_path = Path(target_install_path)
		if not target_install_path.is_absolute():
			raise ValueError('"target-install-path" should be an absolute path')
		
		staging_dir = Path(staging_dir).absolute()
		staging_dir.mkdir(exist_ok=True)
		
		rpmvenv_data = RPMVenvTemplate()
		rpmvenv_data.version = self._version_tag
		rpmvenv_data.release = release_tag
		
		if self.tarball_assets['conf']:
			conf_dir = staging_dir / 'conf'
			conf_dir.mkdir(exist_ok=True)
			for file_path in self.tarball_assets['conf']:
				final_file = Path(move(file_path, conf_dir)).absolute()
				relative_name = final_file.relative_to(staging_dir)
				rpmvenv_data.add_data_file(relative_name, (target_install_path / relative_name).relative_to('/'))
		
		log_dir = staging_dir / 'log'
		log_dir.mkdir(exist_ok=True)
		log_file = log_dir / 'authproxy.log'
		log_file.touch()
		relative_log_name = log_file.relative_to(staging_dir)
		rpmvenv_data.add_data_file(relative_log_name, (target_install_path / relative_log_name).relative_to('/'))
		
		run_dir = staging_dir / 'run'
		run_dir.mkdir(exist_ok=True)
		run_empty_file = run_dir / '.empty_file'
		run_empty_file.touch()
		relative_run_name = run_empty_file.relative_to(staging_dir)
		rpmvenv_data.add_data_file(relative_run_name, (target_install_path / relative_run_name).relative_to('/'))
		
		requirements_file = staging_dir / 'requirements.txt'
		requirements_file.write_text(self.requirements)
		
		rpmvenv_data['python_venv']['name'] = target_install_path.name
		rpmvenv_data['python_venv']['path'] = target_install_path.parent.relative_to('/')
		rpmvenv_data['python_venv']['requirements'] = [requirements_file.relative_to(staging_dir)]
		
		rpmvenv_json_file = staging_dir / '{}.{}.json'.format(rpmvenv_data.name, rpmvenv_data.version)
		rpmvenv_json_file.write_text(str(rpmvenv_data))
		
		rpms_dir = (Path.cwd() if rpms_dir is None else Path(rpms_dir)).absolute()
		rpms_dir.mkdir(exist_ok=True)
		
		return run(('rpmvenv', '--destination', str(rpms_dir), str(rpmvenv_json_file)), stderr=STDOUT, stdout=PIPE, text=True, check=True, cwd=staging_dir).stdout
	
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
	
	@classmethod
	def run_in_docker(cls, version_tag, release_tag, dist_dir='dist', *, target_install_path=DEFAULT_TARGET_INSTALL_PATH):
		"""
		
		"""
		
		host_dist_dir = Path(dist_dir)
		if not host_dist_dir.is_absolute():
			host_dist_dir = (Path.cwd() / host_dist_dir).absolute()
		host_dist_dir.mkdir(parents=True, exist_ok=True)
		
		dist_volume = '/root/dist'
		template_details = {
			'version_tag': version_tag,
			'release_tag': release_tag,
			'target_install_path': target_install_path,
			'dist_dir': str(dist_volume),
		}
		volumes = {str(host_dist_dir): {'bind': str(dist_volume), 'mode': 'rw'}}
		return DockerfileTemplate(**template_details).run(volumes=volumes).decode('utf8')
