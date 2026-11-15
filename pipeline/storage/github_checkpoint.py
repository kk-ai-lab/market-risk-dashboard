"""Fail-closed Git Data checkpoints with optimistic, single-parent ref updates.

Provision the branch and checkpoint file explicitly before using this adapter.
An uncertain write must be reconciled by a fresh read, never blindly retried.
expectedHeadOid cannot detect an A-to-B-to-A ref change. The checkpoint branch
must not be reset, deleted, or recreated while writers can be active.
"""
from __future__ import annotations

import base64
import binascii
import hashlib
import re
from dataclasses import dataclass
from urllib.parse import quote, unquote

import httpx


class CheckpointError(Exception):
    """Missing or invalid checkpoint, or a rejected GitHub operation."""


class CheckpointConflict(CheckpointError):
    """Another writer advanced the branch. Read again before making a decision."""


class CheckpointUncertain(CheckpointError):
    """The transport or server failed; a write may already have taken effect."""


@dataclass(frozen=True)
class CheckpointSnapshot:
    head_sha: str
    tree_sha: str
    path: str
    content: bytes


def _sha(value: object) -> str:
    if not isinstance(value, str) or re.fullmatch(r"[0-9a-f]{40}", value) is None:
        raise CheckpointError("GitHub returned an invalid object SHA")
    return value


def _object(value: object, kind: str) -> str:
    if not isinstance(value, dict) or value.get("type") != kind:
        raise CheckpointError("GitHub returned an unexpected object type")
    return _sha(value.get("sha"))


class GitHubCheckpoint:
    def __init__(self, owner: str, repo: str, branch: str, token: str, *,
                 transport: httpx.BaseTransport | None = None, timeout: float = 30):
        if any(re.fullmatch(r"[A-Za-z0-9_.-]+", part) is None for part in (owner, repo)):
            raise ValueError("Invalid GitHub repository")
        if not branch or branch.startswith("/") or any(c in branch for c in "\r\n?#"):
            raise ValueError("Invalid checkpoint branch")
        if not token or any(c in token for c in "\r\n"):
            raise ValueError("Invalid GitHub credential")
        self._root = f"https://api.github.com/repos/{owner}/{repo}/git"
        self._repository = f"{owner}/{repo}"
        self._branch = branch
        self._ref = "refs/heads/" + quote(branch, safe="/")
        self._client = httpx.Client(
            transport=transport, timeout=timeout, follow_redirects=False, trust_env=False,
            headers={"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json",
                     "X-GitHub-Api-Version": "2022-11-28"},
        )

    def close(self) -> None:
        self._client.close()

    def _request(self, method: str, endpoint: str, payload: dict | None = None) -> dict:
        try:
            response = self._client.request(method, self._root + "/" + endpoint, json=payload)
        except httpx.TransportError:
            raise CheckpointUncertain("GitHub transport outcome is uncertain; reconcile before retry") from None
        if response.status_code >= 500:
            raise CheckpointUncertain("GitHub server outcome is uncertain; reconcile before retry")
        if response.status_code not in {200, 201}:
            raise CheckpointError(f"GitHub checkpoint request rejected (HTTP {response.status_code})")
        try:
            body = response.json()
        except ValueError:
            raise CheckpointError("GitHub returned invalid JSON") from None
        if not isinstance(body, dict):
            raise CheckpointError("GitHub returned an invalid response shape")
        return body

    def read(self, path: str) -> CheckpointSnapshot:
        if not path or path.startswith("/") or any(p in {"", ".", ".."} for p in path.split("/")):
            raise ValueError("Invalid checkpoint path")
        ref = self._request("GET", self._ref.replace("refs/", "ref/", 1))
        if ref.get("ref") != unquote(self._ref):
            raise CheckpointError("GitHub returned an unexpected ref")
        head = _object(ref.get("object"), "commit")
        commit = self._request("GET", f"commits/{head}")
        if _sha(commit.get("sha")) != head:
            raise CheckpointError("GitHub returned a different commit")
        tree = commit.get("tree")
        if not isinstance(tree, dict):
            raise CheckpointError("GitHub returned an invalid commit tree")
        root = _sha(tree.get("sha"))
        current = root
        parts = path.split("/")
        for index, part in enumerate(parts):
            listing = self._request("GET", f"trees/{current}")
            if _sha(listing.get("sha")) != current or listing.get("truncated") is not False:
                raise CheckpointError("GitHub returned an incomplete or different tree")
            entries = listing.get("tree")
            if not isinstance(entries, list):
                raise CheckpointError("GitHub returned invalid tree entries")
            matches = [entry for entry in entries if isinstance(entry, dict) and entry.get("path") == part]
            if len(matches) != 1:
                raise CheckpointError("Checkpoint path is missing or ambiguous")
            entry = matches[0]
            final = index == len(parts) - 1
            current = _object(entry, "blob" if final else "tree")
            if entry.get("mode") not in ({"100644"} if final else {"040000"}):
                raise CheckpointError("Checkpoint path has an unsupported mode")
        blob = self._request("GET", f"blobs/{current}")
        if _sha(blob.get("sha")) != current or blob.get("encoding") != "base64":
            raise CheckpointError("GitHub returned an invalid blob")
        try:
            content = base64.b64decode("".join(blob["content"].split()), validate=True)
        except (KeyError, TypeError, AttributeError, ValueError, binascii.Error):
            raise CheckpointError("GitHub returned invalid blob content") from None
        digest = hashlib.sha1(b"blob " + str(len(content)).encode() + b"\0" + content).hexdigest()
        if digest != current or blob.get("size") != len(content):
            raise CheckpointError("GitHub blob content does not match its identity")
        return CheckpointSnapshot(head, root, path, content)

    def write(self, snapshot: CheckpointSnapshot, content: bytes, message: str) -> str:
        head = _sha(snapshot.head_sha)
        _sha(snapshot.tree_sha)
        query = """mutation($input: CreateCommitOnBranchInput!) {
          createCommitOnBranch(input: $input) {
            commit { oid parents(first: 2) { totalCount nodes { oid } } }
            ref { name target { oid } }
          }
        }"""
        variables = {"input": {
            "branch": {"repositoryNameWithOwner": self._repository, "refName": self._branch},
            "expectedHeadOid": head,
            "message": {"headline": message},
            "fileChanges": {"additions": [{"path": snapshot.path,
                "contents": base64.b64encode(content).decode()}]},
        }}
        try:
            response = self._client.post("https://api.github.com/graphql", json={"query": query, "variables": variables})
        except httpx.TransportError:
            raise CheckpointUncertain("GitHub write outcome is uncertain; reconcile before retry") from None
        if response.status_code in {401, 403, 404}:
            raise CheckpointError(f"GitHub checkpoint request rejected (HTTP {response.status_code})")
        if response.status_code != 200:
            raise CheckpointUncertain("GitHub write outcome is uncertain; reconcile before retry")
        try:
            body = response.json()
            # GraphQL errors may accompany a completed mutation; none prove a conflict.
            if not isinstance(body, dict) or body.get("errors"):
                raise ValueError
            result = body["data"]["createCommitOnBranch"]
            commit = result["commit"]
            new_head = _sha(commit["oid"])
            parents = commit["parents"]
            ref = result["ref"]
            if (parents["totalCount"] != 1 or parents["nodes"] != [{"oid": head}]
                    or ref["name"] != self._branch or ref["target"]["oid"] != new_head):
                raise ValueError
        except (ValueError, TypeError, KeyError, CheckpointError):
            raise CheckpointUncertain("GitHub write response is invalid; reconcile before retry") from None
        return new_head
