import hashlib
import shutil
from pathlib import Path
from typing import Union, Optional, Set


def stable_hash(s: Union[str, bytes]):
    sha = hashlib.md5()
    if isinstance(s, str):
        s = s.encode()
    sha.update(s)
    return sha.hexdigest()[:8]


def _is_copy_necessary(src: Path, dst: Path):
    if not src.is_file():
        raise ValueError(f"File {src} does not exist and cannot be copied.")
    if not dst.is_file():
        return True
    with src.open('rb') as src_fh, dst.open('rb') as dst_fh:
        while True:
            src_content = src_fh.read(4096)
            dst_content = dst_fh.read(4096)
            if not src_content and not dst_content:
                # End of File
                return False
            if src_content != dst_content:
                # Different content
                return True


def copy_if_necessary(src: Path, dst: Path):
    if not _is_copy_necessary(src, dst):
        return
    dst.parent.mkdir(exist_ok=True)
    shutil.copy2(src, dst)


def copytree(src: Path, dst: Path, ignore: Optional[Set[Path]] = None):
    ignore = ignore or set()
    dst.mkdir(exist_ok=True)
    for src_path in src.iterdir():
        dst_path = dst / src_path.name
        if src_path in ignore:
            continue
        if src_path.is_dir():
            copytree(src_path, dst_path, ignore=ignore)
        else:
            copy_if_necessary(src_path, dst_path)
    shutil.copystat(src, dst)
    return dst
