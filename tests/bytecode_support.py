import importlib.machinery
import importlib
import importlib.util
import sys
from pathlib import Path

LEGACY_MODULE_ALIASES = {
    "test_alert_outbox": {
        "app.alert_manager": "app.alerts.alert_manager",
        "app.rules_engine": "app.alerts.rules_engine",
    },
    "test_camera_seed_config": {
        "app.camera_manager": "app.cameras.camera_manager",
    },
    "test_detection": {
        "app.compliance_engine": "app.detection.compliance_engine",
        "app.detector": "app.detection.detector",
    },
    "test_stream_handler": {
        "app.stream_handler": "app.cameras.stream_handler",
    },
}


def load_compiled_tests(loader, pattern, module_name):
    artifact = Path(__file__).parent / "legacy_test_artifacts" / f"{module_name}.pyc"
    for legacy_name, current_name in LEGACY_MODULE_ALIASES.get(module_name, {}).items():
        module = importlib.import_module(current_name)
        sys.modules[legacy_name] = module
        parent_name, child_name = legacy_name.rsplit(".", 1)
        setattr(importlib.import_module(parent_name), child_name, module)

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
