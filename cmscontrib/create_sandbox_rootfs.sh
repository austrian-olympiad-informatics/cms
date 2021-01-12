#!/bin/bash

set -euxo pipefail

rootfs_dir="$1"

debootstrap --variant=minbase groovy "${rootfs_dir}"
mount -t proc /proc "${rootfs_dir}/proc/"
mount --rbind /sys "${rootfs_dir}/sys/"
mount --rbind /dev "${rootfs_dir}/dev/"

cat << EOF | chroot "${rootfs_dir}" /bin/bash
set -euxo pipefail
mkdir box
chmod 777 box
apt-get install --no-install-recommends -y \
  build-essential \
  default-jdk-headless
EOF
