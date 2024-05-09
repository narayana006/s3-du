import boto3
import pytest
from botocore.stub import Stubber


@pytest.fixture(autouse=True)
def isolated_aws(monkeypatch, tmp_path):
    """Dummy credentials and empty config files, so no test can reach a real account."""
    for var in ("AWS_PROFILE", "AWS_DEFAULT_PROFILE", "AWS_SESSION_TOKEN", "AWS_ENDPOINT_URL", "AWS_REGION"):
        monkeypatch.delenv(var, raising=False)
    (tmp_path / "config").write_text("")
    (tmp_path / "credentials").write_text("")
    monkeypatch.setenv("AWS_CONFIG_FILE", str(tmp_path / "config"))
    monkeypatch.setenv("AWS_SHARED_CREDENTIALS_FILE", str(tmp_path / "credentials"))
    monkeypatch.setenv("AWS_EC2_METADATA_DISABLED", "true")
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "testing")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "testing")
    monkeypatch.setenv("AWS_DEFAULT_REGION", "us-east-1")


class StubbedSession:
    """Stands in for boto3.Session; every client answers only from a botocore Stubber."""

    def __init__(self):
        self.clients = {}
        self.stubs = {}

    def stub(self, service, region="us-east-1"):
        key = (service, region)
        if key not in self.clients:
            self.clients[key] = boto3.client(service, region_name=region)
            self.stubs[key] = Stubber(self.clients[key])
            self.stubs[key].activate()
        return self.stubs[key]

    def client(self, service, region_name=None):
        self.stub(service, region_name or "us-east-1")
        return self.clients[(service, region_name or "us-east-1")]

    def assert_all_used(self):
        for stub in self.stubs.values():
            stub.assert_no_pending_responses()


@pytest.fixture
def stubbed():
    return StubbedSession()
