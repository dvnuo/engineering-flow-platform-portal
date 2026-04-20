import httpx

from app.services.provider_config_resolver import GithubProviderConfig


class GithubPrReviewPoller:
    async def poll_review_requests(
        self,
        *,
        provider_config: GithubProviderConfig,
        owner: str,
        repo: str,
        review_target_type: str,
        review_target: str,
    ) -> list[dict]:
        if review_target_type == "team":
            qualifier = f"team-review-requested:{review_target}"
        else:
            qualifier = f"review-requested:{review_target}"
        query = f"repo:{owner}/{repo} is:pr is:open {qualifier}"

        headers = {
            "Authorization": f"Bearer {provider_config.api_token}",
            "Accept": "application/vnd.github+json",
        }
        base_url = provider_config.base_url.rstrip("/")
        items: list[dict] = []

        async with httpx.AsyncClient(timeout=30.0) as client:
            search_resp = await client.get(
                f"{base_url}/search/issues",
                params={"q": query, "per_page": 50},
                headers=headers,
            )
            if search_resp.status_code >= 400:
                raise ValueError(f"GitHub search API request failed with status {search_resp.status_code}")
            search_data = search_resp.json() if search_resp.content else {}
            raw_items = search_data.get("items") if isinstance(search_data, dict) else []

            for item in raw_items or []:
                pull_number = item.get("number")
                if pull_number is None:
                    continue
                detail_resp = await client.get(
                    f"{base_url}/repos/{owner}/{repo}/pulls/{pull_number}",
                    headers=headers,
                )
                if detail_resp.status_code >= 400:
                    raise ValueError(f"GitHub pull details API request failed with status {detail_resp.status_code}")
                pull_obj = detail_resp.json() if detail_resp.content else {}
                head_obj = pull_obj.get("head") if isinstance(pull_obj, dict) else {}
                head_sha = (head_obj or {}).get("sha") if isinstance(head_obj, dict) else None
                items.append(
                    {
                        "owner": owner,
                        "repo": repo,
                        "pull_number": pull_number,
                        "html_url": item.get("html_url"),
                        "title": item.get("title"),
                        "head_sha": head_sha,
                        "review_target": {"type": review_target_type, "name": review_target},
                        "source_payload": item,
                    }
                )

        return items
