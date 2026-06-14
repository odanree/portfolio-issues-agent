"""GitHubClient — POST /repos/{owner}/{repo}/issues."""
import httpx
import pytest
import respx

from app.services.github_client import GitHubClient


@respx.mock
@pytest.mark.asyncio
async def test_create_issue_returns_url_and_number():
    route = respx.post("https://api.github.com/repos/odanree/parking-enforcement-detector/issues").mock(
        return_value=httpx.Response(
            201,
            json={
                "html_url": "https://github.com/odanree/parking-enforcement-detector/issues/7",
                "number": 7,
            },
        )
    )
    client = GitHubClient(token="ghp-test")
    out = await client.create_issue(
        owner="odanree",
        repo="parking-enforcement-detector",
        title="Update README to mention ChromaDB hard-negative mining",
        body="…",
        labels=["documentation"],
    )
    await client.close()
    assert out["number"] == 7
    assert "issues/7" in out["html_url"]
    assert route.called
    auth = route.calls[0].request.headers.get("authorization")
    assert auth == "Bearer ghp-test"


@respx.mock
@pytest.mark.asyncio
async def test_create_issue_raises_on_non_2xx():
    respx.post("https://api.github.com/repos/x/y/issues").mock(
        return_value=httpx.Response(422, json={"message": "Validation Failed"})
    )
    client = GitHubClient(token="ghp-test")
    with pytest.raises(RuntimeError, match="GitHub 422"):
        await client.create_issue(owner="x", repo="y", title="t", body="b")
    await client.close()


@pytest.mark.asyncio
async def test_create_issue_requires_token():
    client = GitHubClient(token="")
    with pytest.raises(RuntimeError, match="GITHUB_TOKEN"):
        await client.create_issue(owner="x", repo="y", title="t", body="b")
    await client.close()
