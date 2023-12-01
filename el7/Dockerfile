FROM centos:7

RUN yum -y upgrade
RUN yum -y install rpmdevtools yum-utils epel-release

WORKDIR /root
RUN rpmdev-setuptree
VOLUME ["/root/rpmbuild/SRPMS", "/root/rpmbuild/RPMS"]

WORKDIR /root/rpmbuild/SPECS/
COPY *.spec ./

RUN for spec_file in *.spec; do yum-builddep -y "$spec_file"; done
RUN for spec_file in *.spec; do spectool -g -R "$spec_file"; done
COPY *.patch /root/rpmbuild/SOURCES/

ENTRYPOINT ["rpmbuild", "-ba"]
