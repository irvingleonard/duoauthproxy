#!/bin/bash

yum -y install rpmdevtools yum-utils

rpmdev-setuptree

for spec_files in *.spec; do
	
	if [ -e "$spec_files" ]; then
		mv *.spec rpmbuild/SPECS/
	else
		echo "No new specs files were found"
	fi
    
    break
done

yum-builddep -y rpmbuild/SPECS/python38-altinstall-duoauthproxy.spec
spectool -g -R rpmbuild/SPECS/python38-altinstall-duoauthproxy.spec
QA_RPATHS=$[ 0x0002|0x0010 ] rpmbuild -ba rpmbuild/SPECS/python38-altinstall-duoauthproxy.spec

yum -y install rpmbuild/RPMS/x86_64/python38-altinstall-duoauthproxy-3.8.4-5.el7.x86_64.rpm
/usr/local/bin/python3 build-rpms.py --openssl-dist /usr/local/openssl/ --show-output 1.el7
