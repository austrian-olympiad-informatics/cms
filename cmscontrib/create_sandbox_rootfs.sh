#!/bin/bash

set -euxo pipefail

rootfs_dir="$1"

debootstrap --variant=minbase --include=build-essential,default-jdk-headless hirsute "${rootfs_dir}"
