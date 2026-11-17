"""Stable private storage, including the existing Desktop Aurora Memory layout."""
from pathlib import Path


def data_root(root, home=None):
    home = Path(home) if home is not None else Path.home()
    local = Path(root) / 'data'
    # An installation explicitly linked by its owner remains authoritative.
    if local.is_symlink():
        target = local.resolve()
        if not target.is_dir():
            raise RuntimeError(f'Aurora 数据目录链接失效：{local} → {target}。请恢复原数据目录后再启动。')
        return target
    desktop = home / 'Desktop' / 'Aurora Memory'
    for candidate in (desktop, local, home / 'Library' / 'Application Support' / 'Aurora'):
        if any((candidate / name).is_file() for name in ('accounts.sqlite3', 'memory.sqlite3')):
            return candidate
    return home / 'Library' / 'Application Support' / 'Aurora'
