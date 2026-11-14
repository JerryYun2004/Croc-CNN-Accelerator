"""Launch the native, Qt-free KLayout engine with its private dylib directory."""
import os
from pathlib import Path
import re
import subprocess

from cp0c_lvs_support import require, sha256


def native_binary(path):
    """Inspect the compiled file, following symlinks but not executing it."""
    path = Path(path)
    require(path.is_file() and os.access(path, os.X_OK), f'Executable missing: {path}')
    resolved = path.resolve(strict=True)
    description = subprocess.check_output(['file', '-b', '-L', str(path)], text=True).strip()
    require('Mach-O' in description and 'arm64' in description,
            f'Not a native arm64 executable: {path}: {description}')
    return {'path': str(path), 'resolved_path': str(resolved),
            'file_description': description, 'sha256': sha256(resolved)}


def yosys_runtime(launcher):
    """Audit OSS CAD Suite's Darwin launcher and payload; keep using the launcher.

    The 2025-12-30 package puts compiled programs under libexec and shell
    launchers under bin. The launcher supplies Tcl/Python/resource environment
    variables, so calling the compiled program directly is not equivalent.
    """
    launcher = Path(launcher)
    require(launcher.is_file() and os.access(launcher, os.X_OK), f'Yosys launcher missing: {launcher}')
    description = subprocess.check_output(['file', '-b', '-L', str(launcher)], text=True).strip()
    if 'Mach-O' in description:
        return {'kind': 'direct-native-binary', 'execution_path': str(launcher),
                'files': [native_binary(launcher)]}
    text = launcher.read_text()
    require(text.startswith('#!') and 'shell script' in description,
            f'Unrecognized Yosys executable format: {launcher}: {description}')
    # This is the exec target emitted by the pinned suite packaging script.
    # Inspect the known layout rather than evaluating an arbitrary shell script.
    target = 'exec "$release_topdir_abs"/libexec/yosys "$@"'
    exec_lines = re.findall(r'^\s*(exec\s+[^\n]+)\s*$', text, re.M)
    require(exec_lines == [target], f'Unrecognized OSS CAD Suite Yosys launcher target: {launcher}')
    require('/libexec/realpath' in text and 'release_topdir_abs=' in text,
            f'Missing OSS CAD Suite launcher path setup: {launcher}')
    suite = launcher.parent.parent
    files = [{'path': str(launcher), 'resolved_path': str(launcher.resolve()),
              'file_description': description, 'sha256': sha256(launcher)}]
    files += [native_binary(suite/'libexec/yosys'), native_binary(suite/'libexec/realpath')]
    return {'kind': 'oss-cad-suite-darwin-launcher', 'execution_path': str(launcher), 'files': files}


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
