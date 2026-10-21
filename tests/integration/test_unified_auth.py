"""Opt-in auth gate against the actual production image and real PG/Redis."""
import json
import os
import subprocess
import pytest

@pytest.mark.integration
def test_production_image_auth_contract():
    container=os.getenv('BRAINAPI_AUTH_TEST_CONTAINER')
    if not container:pytest.skip('Set BRAINAPI_AUTH_TEST_CONTAINER to a disposable production container')
    environment=dict(os.environ)
    for name in ['DOCKER_HOST','DOCKER_CONTEXT','DOCKER_TLS','DOCKER_TLS_VERIFY','DOCKER_CERT_PATH']:environment.pop(name,None)
    result=subprocess.run(['docker','--host=unix:///var/run/docker.sock','exec',container,'bash','/app/entrypoint.sh','--qualify-auth'],env=environment,capture_output=True,text=True,timeout=300)
    assert result.returncode==0,result.stdout+result.stderr
    report=json.loads(result.stdout.strip().splitlines()[-1])
    assert report['result']=='passed'
    assert report['mcp_auth_parity_permissions']=='passed'
    assert report['revocation_all_surfaces']=='passed'
    assert report['validation_422_no_polling']=='passed'
