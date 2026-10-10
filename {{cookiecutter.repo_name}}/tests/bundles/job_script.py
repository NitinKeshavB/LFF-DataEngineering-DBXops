"""Import a bundle's job script as a module; job scripts are not part of the package."""

import importlib.util
import sys
from contextlib import contextmanager
from pathlib import Path


@contextmanager
def loaded_script(path: Path):
    """Yield the script at `path` as a module registered in sys.modules (dataclasses look their module up there)."""
    spec = importlib.util.spec_from_file_location(path.stem, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    try:
        spec.loader.exec_module(module)
        yield module
    finally:
        del sys.modules[spec.name]
