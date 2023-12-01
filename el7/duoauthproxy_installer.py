# python

import logging
import pathlib
import tarfile
import tempfile
import urllib.parse

import requests
import simplifiedapp

LOGGER = logging.getLogger(__name__)

PACKAGE_NAME_MAP = {
	'duo_client_python'	: 'duo-client',
	'service_identity'	: 'service-identity',
	'setuptools_scm'	: 'setuptools-scm',
}

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

def get_wheels(tarball, output_dir = './wheels', ignore_packages = ['python-'], overwrite_wheels = False, resolve_upstream_names = True):
	'''Get wheels
	Given a tarball, detect all the packages in wheels present and build the ones that live as source trees. Extract/put all wheels in the "output_dir" 
	'''
	
	tarball = pathlib.Path(tarball)
	tarball_obj = tarfile.open(name = tarball)
	LOGGER.info('Identifying packages from tarball: %s', tarball)
	
	output_dir = pathlib.Path(output_dir)
	LOGGER.debug('Confirming output directory: %s', output_dir)
	output_dir.mkdir(parents = True, exist_ok = True)
	
	tarball_names = tarball_obj.getnames()
	tarball_root = pathlib.Path(tarball_names[0]).parts[0]
	LOGGER.debug('Got tarball root (the directory within) to be: %s', tarball_root)
	
	third_party_dir = pathlib.Path(tarball_root) / 'pkgs'
	packages = set()
	for tarball_name in tarball_names:
		tarball_name = pathlib.Path(tarball_name)
		if third_party_dir in tarball_name.parents:
			module_name = tarball_name.relative_to(third_party_dir).parts[0]
			packages.add(module_name)
	
	remove_packages = []
	for ignoring_package in ignore_packages:
		for package in packages:
			if package[:len(ignoring_package)].lower() == ignoring_package.lower():
				remove_packages.append(package)
				break
	
	packages = [package for package in packages if package not in remove_packages]
	wheels_present = [package for package in packages if package[-4:] == '.whl']
	
	with tempfile.TemporaryDirectory() as workdir:
		for wheel in wheels_present:
			tarball_obj.extract(third_party_dir / wheel, path = workdir, set_attrs = False)
		return list(workdir.iterdir())
	
	return wheels_present
	
	result = {}
	with tempfile.TemporaryDirectory() as workdir:
		workdir = pathlib.Path(workdir)
		tarball_obj.extractall(workdir)
		pkgs_dir = workdir / pathlib.Path(tarball).stem / 'pkgs'
	
		for child in pkgs_dir.iterdir():
			if child.is_dir():
				name, ignore_this, version = child.name.rpartition('-')
				if child.name.lower() == 'duoauthproxy':
					name = 'duoauthproxy'
					version = tarball.name.split('-', maxsplit = 2)[1]
				elif resolve_upstream_names and (name in PACKAGE_NAME_MAP):
					name = PACKAGE_NAME_MAP[name]
				result[name] = version
			elif child.is_file():
				result[child.name] = None
			else:
				raise RuntimeError("Don't know what the included package is: {}".format(child.name))
	
	return result

if __name__ == '__main__':
	simplifiedapp.main()