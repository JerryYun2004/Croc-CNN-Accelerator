"""Launch the native, Qt-free KLayout engine with its private dylib directory."""
import os
from pathlib import Path


def klayout_environment(tool, base=None):
    """Return a child-only environment; never change the caller's environment.

    The pinned qmake build records bare libklayout_*.0.dylib install names.
    An LC_RPATH alone cannot resolve these names. Set the search directory
    immediately before directly executing strmrun, without a shell intermediary.
    """
    tool = Path(tool).resolve(strict=True)
    if not tool.is_file() or not os.access(tool, os.X_OK):
        raise RuntimeError(f'KLayout executable is missing or not executable: {tool}')
    directory = tool.parent
    if ':' in str(directory):
        raise RuntimeError('KLayout install path cannot contain a colon (dyld path separator).')
    for name in ('bd', 'db', 'pex', 'tl', 'gsi', 'lib', 'rdb', 'lym',
                 'rba', 'drc', 'lvs', 'pya'):
        library = directory / f'libklayout_{name}.0.dylib'
        if not library.is_file():
            raise RuntimeError(f'KLayout private library missing: {library}')
    env = dict(os.environ if base is None else base)
    env['DYLD_LIBRARY_PATH'] = str(directory)
    return env
