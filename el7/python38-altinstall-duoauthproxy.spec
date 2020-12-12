%{!?python_sitearch: %global python_sitearch %(%{__python} -c "from distutils.sysconfig import get_python_lib; print(get_python_lib(1))")}
%define debug_package %{nil}
%global __os_install_post %(echo '%{__os_install_post}' | sed -e 's!/usr/lib[^[:space:]]*/brp-python-bytecompile[[:space:]].*$!!g')
%define _duo_version 5.1.1
%define _duo_source_commit 7484191
%define _duo_source_directory duoauthproxy-%{_duo_version}-%{_duo_source_commit}-src

Name:           python38-altinstall-duoauthproxy
Version:        3.8.4
Release:        5%{?dist}
Summary:        Interpreter of the Python programming language

License:        Python
URL:            https://www.python.org/
Source0:        https://dl.duosecurity.com/duoauthproxy-%{_duo_version}-src.tgz

BuildRequires:  chrpath
BuildRequires:  gcc
BuildRequires:  libffi-devel
BuildRequires:  make
BuildRequires:  perl
BuildRequires:  zlib-devel
Provides:		python38-altinstall
Conflicts:		python38-altinstall
Provides:       /usr/local/bin/python3.8
# Don't even bother with default python. It's required by this package, that's why is listed here.
Provides:       /usr/local/bin/python

%description
Python is an accessible, high-level, dynamically typed, interpreted programming
language, designed with an emphasis on code readability.
It includes an extensive standard library, and has a vast ecosystem of
third-party libraries.

This is an apparently customized python version shipped with duoauthproxy-%{_duo_version}

%prep
%setup -q -n %{_duo_source_directory}


%build
make python


%install
mv duoauthproxy-build/* %{buildroot}
chrpath -r /usr/local/openssl/lib %{buildroot}/usr/local/openssl/bin/openssl
chrpath -r /usr/local/lib %{buildroot}/usr/local/lib/python3.8/lib-dynload/*.so
for openssl_lib in %{buildroot}/usr/local/openssl/lib/*.a; do
	ln -s ../openssl/lib/$(basename $openssl_lib) %{buildroot}/usr/local/lib/$(basename $openssl_lib)
done
for openssl_lib in %{buildroot}/usr/local/openssl/lib/*.so; do
	ln -s ../openssl/lib/$(basename $openssl_lib) %{buildroot}/usr/local/lib/$(basename $openssl_lib)
done
cp %{_builddir}/%{_duo_source_directory}/*.py %{buildroot}/usr/local/lib/python3.8/
# Compress man page
%{__gzip} --name --best %{buildroot}/usr/local/share/man/man1/python3.8.1
rm %{buildroot}/usr/local/share/man/man1/python3.1

 
%files
/usr/local/bin/2to3
/usr/local/bin/2to3-3.8
/usr/local/bin/idle3
/usr/local/bin/idle3.8
/usr/local/bin/pydoc3
/usr/local/bin/pydoc3.8
/usr/local/bin/python3
/usr/local/bin/python3-config
/usr/local/bin/python3.8
/usr/local/bin/python3.8-config
/usr/local/include/python3.8
/usr/local/lib
/usr/local/openssl
%doc /usr/local/share/man/man1/python3.8.1.gz

%changelog
* Mon Dec 7 2020 Irving Leonard <mm-irvingleonard@github.com> 5.1.1-1
- Upgraded to duoauthproxy 5.1.1
* Tue Nov 10 2020 Irving Leonard <mm-irvingleonard@github.com> 5.1.0-1
- Upgraded to duoauthproxy 5.1.0
* Wed Sep 30 2020 Irving Leonard <mm-irvingleonard@github.com> 5.0.2-1
- Upgraded to duoauthproxy 5.0.2
* Wed Aug 19 2020 Irving Leonard <mm-irvingleonard@github.com> 5.0.0-2
- Upgraded to duoauthproxy 5.0.0
* Wed Jul 22 2020 Irving Leonard <mm-irvingleonard@github.com> 4.0.2-1
- Upgraded to duoauthproxy 4.0.2
* Fri Jul 17 2020 Irving Leonard <mm-irvingleonard@github.com> 4.0.1-1
- Initial RPM release
