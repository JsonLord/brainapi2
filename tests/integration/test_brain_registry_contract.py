"""Opt-in gate against a disposable local HF CPU container, never the live Space."""
import json
import os
import subprocess

import pytest


@pytest.mark.integration
def test_real_registry_storage_contract():
    container = os.getenv('BRAINAPI_REGISTRY_TEST_CONTAINER')
    if not container:
        pytest.skip('Set BRAINAPI_REGISTRY_TEST_CONTAINER to a disposable HF CPU container')
    environment = dict(os.environ)
    for selector in ('DOCKER_HOST', 'DOCKER_CONTEXT', 'DOCKER_TLS', 'DOCKER_TLS_VERIFY', 'DOCKER_CERT_PATH'):
        environment.pop(selector, None)
    result = subprocess.run(
        ['docker', '--host=unix:///var/run/docker.sock', 'exec', container,
         'bash', '/app/entrypoint.sh', '--qualify-registry'],
        env=environment, capture_output=True, text=True, timeout=300, check=True,
    )
    report = json.loads(result.stdout.strip().splitlines()[-1])
    assert report['result'] == 'passed'
    for operation in ('entities', 'search', 'context'):
        assert report['unknown_' + operation] == '404 BRAIN_NOT_FOUND; storage/cache unchanged'
    assert report['storage_duplicate_race'] == 'one create, one domain conflict'
    assert report['orphan_cleanup'] == 'physical database removed without registry creation'
