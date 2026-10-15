import importlib.util
import json

import pytest

from capopt import instance as I


@pytest.fixture(scope="session")
def referee():
    spec = importlib.util.spec_from_file_location("referee", I.PACK / "reference" / "evaluate.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="session")
def instances():
    return {n: I.load(n) for n in I.INSTANCE_NAMES}


@pytest.fixture(scope="session")
def best_known():
    return {n: I.best_known(n) for n in I.INSTANCE_NAMES}


def write_instance(tmp_path, raw):
    p = tmp_path / f"{raw['name']}.json"
    p.write_text(json.dumps(raw), encoding="utf-8")
    return p
