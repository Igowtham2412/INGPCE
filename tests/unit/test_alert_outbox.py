from tests.bytecode_support import load_compiled_tests


def load_tests(loader, tests, pattern):
    return load_compiled_tests(loader, pattern, "test_alert_outbox")
