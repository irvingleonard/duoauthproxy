# RHEL/CentOS 7 and compatibles

In order to build the package you need to copy all the files in this directory to a directory with enough free space in your build host (/tmp might not be big enough). Then
```
chmod +x build_rpms.sh
./build_rpms.sh
```
the source RPM will end up in rpmbuild/SRPMS and the binary one in rpmbuild/RPMS
