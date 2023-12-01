%{!?python_sitearch: %global python_sitearch %(%{__python} -c "from distutils.sysconfig import get_python_lib; print(get_python_lib(1))")}
%define debug_package %{nil}
%global __os_install_post %(echo '%{__os_install_post}' | sed -e 's!/usr/lib[^[:space:]]*/brp-python-bytecompile[[:space:]].*$!!g')

Name:           duoauthproxy
Version:        6.2.0
Release:        1%{?dist}
Summary:        On-premises service that receives authentication requests from your local devices and applications via RADIUS or LDAP

License:        Unknown
URL:            https://duo.com/docs/authproxy-overview
Source0:        https://dl.duosecurity.com/%{name}-%{version}-src.tgz

BuildRequires:  bzip2-devel
BuildRequires:  chkconfig
BuildRequires:  diffutils
BuildRequires:  gcc
BuildRequires:  gcc-c++
BuildRequires:  gdbm-devel
BuildRequires:  libffi-devel
BuildRequires:  libuuid-devel
BuildRequires:  make
BuildRequires:  openssl-devel
BuildRequires:  perl
BuildRequires:  python3
BuildRequires:  readline-devel
BuildRequires:  selinux-policy-devel
BuildRequires:  sqlite-devel
BuildRequires:  tk-devel
BuildRequires:  uuid-devel
BuildRequires:  xz-devel

%description
The Duo Authentication Proxy is an on-premises software service that receives 
authentication requests from your local devices and applications via RADIUS or 
LDAP, optionally performs primary authentication against your existing LDAP 
directory or RADIUS authentication server, and then contacts Duo to perform 
secondary authentication. Once the user approves the two-factor request 
(received as a push notification from Duo Mobile, or as a phone call, etc.), the 
Duo proxy returns access approval to the requesting device or application.

%prep
%setup -q -n %{name}-%{version}-src

%build
env CXX=/usr/bin/c++ make

%install
cd duoauthproxy-build
./install --install-dir=%{buildroot}/opt/duoauthproxy --service-user=duo_authproxy_svc --log-group=duo_authproxy_grp --create-init-script=yes --enable-selinux=yes
 
%files


%changelog
* Wed Nov 29 2023 Irving Leonard <mm-irvingleonard@github.com> 6.2.0-1
- Initial RPM release
