import importlib.machinery
import importlib.util
import sys
from pathlib import Path


def load_compiled_tests(loader, pattern, module_name):
    artifact = Path(__file__).parent / "legacy_test_artifacts" / f"{module_name}.pyc"
    compiled_loader = importlib.machinery.SourcelessFileLoader(
        f"tests._compiled_{module_name}", str(artifact)
    )
    spec = importlib.util.spec_from_loader(compiled_loader.name, compiled_loader)
    if spec is None:
        raise ImportError(f"Unable to load preserved test artifact: {artifact}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    try:
        compiled_loader.exec_module(module)
    except Exception:
        sys.modules.pop(spec.name, None)
        raise
    return loader.loadTestsFromModule(module, pattern=pattern)
