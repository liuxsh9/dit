from dit.core.remote import RemoteClient


class TestRemoteClientProxyURL:
    def test_forgejo_proxy_url_format(self):
        rc = RemoteClient(
            base_url="http://forgejo:3000",
            token="mytoken",
            repo="alice/mydata",
        )
        assert rc._refs_url("heads", "main") == (
            "http://forgejo:3000/api/v1/repos/alice/mydata/refs/heads/main"
        )

    def test_auth_header_is_token_format(self):
        rc = RemoteClient(
            base_url="http://forgejo:3000",
            token="abc123",
            repo="alice/repo",
        )
        assert rc.client.headers["Authorization"] == "token abc123"

    def test_objects_url(self):
        rc = RemoteClient(
            base_url="http://forgejo:3000",
            token="t",
            repo="owner/repo",
        )
        assert rc._objects_url("rows", "a" * 64) == (
            f"http://forgejo:3000/api/v1/repos/owner/repo/objects/rows/{'a' * 64}"
        )
