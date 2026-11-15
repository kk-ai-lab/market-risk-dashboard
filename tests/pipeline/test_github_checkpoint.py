import base64
import hashlib
import json
from concurrent.futures import ThreadPoolExecutor
from threading import Lock

import httpx
import pytest

from pipeline.storage.github_checkpoint import (
    CheckpointConflict,
    CheckpointError,
    CheckpointUncertain,
    GitHubCheckpoint,
)


def sha(content):
    return hashlib.sha1(b"blob " + str(len(content)).encode() + b"\0" + content).hexdigest()


class Server:
    def __init__(self):
        self.head = "1" * 40
        self.root = "2" * 40
        self.content = b"initial"
        self.blobs = {sha(self.content): self.content}
        self.trees = {self.root: {"state.json": sha(self.content), "other.txt": "3" * 40}}
        self.commits = {self.head: {"sha": self.head, "tree": {"sha": self.root}}}
        self.calls = []
        self.lock = Lock()

    def __call__(self, request):
        assert request.url.scheme == "https" and request.url.host == "api.github.com"
        with self.lock:
            return self.handle(request)

    def handle(self, request):
        endpoint = request.url.path.split("/git/", 1)[1] if "/git/" in request.url.path else "graphql"
        data = json.loads(request.content) if request.content else None
        self.calls.append((request.method, endpoint, data))
        result = {}
        if endpoint == "graphql":
            assert request.method == "POST"
            assert "createCommitOnBranch" in data["query"]
            inputs = data["variables"]["input"]
            assert inputs["branch"] == {"repositoryNameWithOwner": "owner/repo", "refName": "checkpoint"}
            if inputs["expectedHeadOid"] != self.head:
                return httpx.Response(200, json={"errors": [{"type": "STALE_DATA"}], "data": {"createCommitOnBranch": None}})
            additions = inputs["fileChanges"]["additions"]
            assert len(additions) == 1
            content = base64.b64decode(additions[0]["contents"])
            blob = sha(content)
            self.blobs[blob] = content
            tree = f"{len(self.trees) + 10:040x}"
            self.trees[tree] = dict(self.trees[self.commits[self.head]["tree"]["sha"]])
            self.trees[tree][additions[0]["path"]] = blob
            old_head = self.head
            self.head = f"{len(self.commits) + 20:040x}"
            self.commits[self.head] = {"sha": self.head, "tree": {"sha": tree}, "parents": [{"sha": old_head}]}
            result = {"data": {"createCommitOnBranch": {
                "commit": {"oid": self.head, "parents": {"totalCount": 1, "nodes": [{"oid": old_head}]}},
                "ref": {"name": "checkpoint", "target": {"oid": self.head}},
            }}}
        elif endpoint == "ref/heads/checkpoint":
            result = {"ref": "refs/heads/checkpoint", "object": {"type": "commit", "sha": self.head}}
        elif request.method == "GET" and endpoint.startswith("commits/"):
            result = self.commits[endpoint.split("/")[1]]
        elif request.method == "GET" and endpoint.startswith("trees/"):
            key = endpoint.split("/")[1]
            result = {"sha": key, "truncated": False, "tree": [
                {"path": path, "type": "blob", "mode": "100644", "sha": value}
                for path, value in self.trees[key].items()]}
        elif request.method == "GET" and endpoint.startswith("blobs/"):
            key = endpoint.split("/")[1]
            content = self.blobs[key]
            result = {"sha": key, "size": len(content), "encoding": "base64", "content": base64.b64encode(content).decode()}
        return httpx.Response(200, json=result)


def client(server):
    return GitHubCheckpoint("owner", "repo", "checkpoint", "secret-token", transport=httpx.MockTransport(server))


def test_fixed_head_single_parent_and_preserved_files():
    server = Server()
    adapter = client(server)
    state = adapter.read("state.json")
    assert state.content == b"initial"
    new_head = adapter.write(state, b"new", "Update state")
    assert server.commits[new_head]["parents"] == [{"sha": state.head_sha}]
    tree = server.trees[server.commits[new_head]["tree"]["sha"]]
    assert tree["other.txt"] == "3" * 40
    assert adapter.read("state.json").content == b"new"
    assert ("GET", f"commits/{state.head_sha}", None) in server.calls


def test_concurrent_siblings_only_one_advances():
    server = Server()
    adapters = [client(server), client(server)]
    states = [adapter.read("state.json") for adapter in adapters]

    def write(index):
        try:
            return adapters[index].write(states[index], str(index).encode(), "Update")
        except (CheckpointConflict, CheckpointUncertain):
            return "conflict"

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(write, range(2)))
    assert results.count("conflict") == 1
    assert server.head in results
    assert len([call for call in server.calls if call[1] == "graphql"]) == 2


@pytest.mark.parametrize("status", [301, 401, 403, 404, 429])
def test_errors_never_initialize_or_leak_credentials(status, caplog):
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(status, json={"message": "secret-token"})

    with pytest.raises(CheckpointError) as caught:
        client(handler).read("state.json")
    assert len(calls) == 1
    assert "secret-token" not in str(caught.value) + caplog.text
    assert caught.value.__cause__ is None


@pytest.mark.parametrize("failure", ["timeout", "server"])
def test_uncertain_outcome_is_not_retried(failure):
    calls = []

    def handler(request):
        calls.append(request)
        if failure == "timeout":
            raise httpx.ReadTimeout("secret-token", request=request)
        return httpx.Response(503, text="secret-token")

    with pytest.raises(CheckpointUncertain) as caught:
        client(handler).read("state.json")
    assert len(calls) == 1
    assert "secret-token" not in str(caught.value)
    assert caught.value.__cause__ is None
    if failure == "timeout":
        assert caught.value.__suppress_context__


@pytest.mark.parametrize("mutation", ["sha", "type", "missing", "blob", "truncated"])
def test_corrupt_state_fails_closed(mutation):
    server = Server()

    def handler(request):
        response = server(request)
        body = response.json()
        if mutation == "sha" and "/ref/" in request.url.path:
            body["object"]["sha"] = "bad"
        elif mutation == "type" and "/ref/" in request.url.path:
            body["object"]["type"] = "tag"
        elif mutation == "missing" and "/trees/" in request.url.path:
            body["tree"] = []
        elif mutation == "blob" and "/blobs/" in request.url.path:
            body["content"] = "eA=="
        elif mutation == "truncated" and "/trees/" in request.url.path:
            body["truncated"] = True
        return httpx.Response(200, json=body)

    with pytest.raises(CheckpointError):
        client(handler).read("state.json")
    assert all(method == "GET" for method, _, _ in server.calls)


def test_timeout_after_ref_applied_requires_reconciliation():
    server = Server()

    def handler(request):
        response = server(request)
        if request.url.path == "/graphql":
            raise httpx.ReadTimeout("secret-token", request=request)
        return response

    adapter = client(handler)
    before = adapter.read("state.json")
    with pytest.raises(CheckpointUncertain):
        adapter.write(before, b"claimed", "Claim")
    assert server.head != before.head_sha
    assert len([call for call in server.calls if call[1] == "graphql"]) == 1
    assert adapter.read("state.json").content == b"claimed"


@pytest.mark.parametrize("failure", ["invalid_json", "partial", "errors", "parents", "ref"])
def test_invalid_write_response_is_uncertain_after_commit(failure):
    server = Server()

    def handler(request):
        response = server(request)
        if request.url.path != "/graphql":
            return response
        if failure == "invalid_json":
            return httpx.Response(200, text="secret-token")
        body = response.json()
        if failure == "partial":
            body["data"] = None
        elif failure == "errors":
            body["errors"] = [{"message": "secret-token"}]
        elif failure == "parents":
            body["data"]["createCommitOnBranch"]["commit"]["parents"]["totalCount"] = 2
        else:
            body["data"]["createCommitOnBranch"]["ref"] = None
        return httpx.Response(200, json=body)

    adapter = client(handler)
    state = adapter.read("state.json")
    with pytest.raises(CheckpointUncertain) as caught:
        adapter.write(state, b"changed", "Claim")
    assert "secret-token" not in str(caught.value)
    assert server.head != state.head_sha
    assert len([call for call in server.calls if call[1] == "graphql"]) == 1


def test_stale_snapshot_rejected_after_branch_moves_back():
    server = Server()
    adapter = client(server)
    original = adapter.read("state.json")
    newer = adapter.write(original, b"newer", "Update")
    stale = adapter.read("state.json")
    server.head = original.head_sha
    with pytest.raises(CheckpointUncertain):
        adapter.write(stale, b"stale", "Update")
    assert server.head == original.head_sha
    assert newer != server.head


def test_executable_checkpoint_is_rejected():
    server = Server()

    def handler(request):
        response = server(request)
        if "/trees/" in request.url.path:
            body = response.json()
            body["tree"][0]["mode"] = "100755"
            return httpx.Response(200, json=body)
        return response

    with pytest.raises(CheckpointError, match="mode"):
        client(handler).read("state.json")


@pytest.mark.parametrize("status", [408, 409, 422, 429, 500])
def test_rejected_or_failed_write_response_is_uncertain_and_not_retried(status):
    server = Server()

    def handler(request):
        response = server(request)
        if request.url.path == "/graphql":
            return httpx.Response(status, text="secret-token")
        return response

    adapter = client(handler)
    before = adapter.read("state.json")
    with pytest.raises(CheckpointUncertain) as caught:
        adapter.write(before, b"claimed", "Claim")
    assert "secret-token" not in str(caught.value)
    assert server.head != before.head_sha
    assert len([call for call in server.calls if call[1] == "graphql"]) == 1
    assert adapter.read("state.json").content == b"claimed"
