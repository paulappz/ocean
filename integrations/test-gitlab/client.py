import os
import asyncio
import logging
from typing import Any, Dict, List, Optional
from port_ocean.utils import http_async_client
from dotenv import load_dotenv

# Load environment variables from .env file
load_dotenv()

# Load configuration values
GITLAB_API_URL = os.getenv("OCEAN__INTEGRATION__CONFIG__GITLAB_API_URL")
GITLAB_TOKEN = os.getenv("OCEAN__INTEGRATION__CONFIG__GITLAB_TOKEN")
RATE_LIMIT = int(os.getenv("OCEAN__INTEGRATION__CONFIG__RATE_LIMIT", 10))
RATE_LIMIT_PERIOD = int(os.getenv("OCEAN__INTEGRATION__CONFIG__RATE_LIMIT_PERIOD", 60))
WEBHOOK_URL = os.getenv("OCEAN__INTEGRATION__CONFIG__WEBHOOK_URL")

logging.basicConfig(level=logging.INFO)

class GitLabHandler:
    def __init__(self, api_url: str = GITLAB_API_URL, token: str = GITLAB_TOKEN) -> None:
        self.api_url = api_url
        self.token = token
        self.rate_limit = RATE_LIMIT
        self.rate_limit_period = RATE_LIMIT_PERIOD

        if not self.token:
            logging.error("GITLAB_TOKEN is missing. Please set it in the environment.")
            raise ValueError("GITLAB_TOKEN is required for GitLab API access")

    async def _rate_limited_request(self, endpoint: str, params: Optional[Dict[str, Any]] = None) -> List[Dict[str, Any]]:
        """Perform a rate-limited, paginated request to the GitLab API."""
        url = f"{self.api_url}{endpoint}"
        headers = {"Authorization": f"Bearer {self.token}"}
        all_data = []

        while url:
            response = await http_async_client.get(url, params=params, headers=headers, timeout=30)

            if response.status_code == 429:  # Too Many Requests
                logging.warning("Rate limit reached; retrying after delay")
                await asyncio.sleep(self.rate_limit_period)
                continue

            response.raise_for_status()
            data = response.json()
            all_data.extend(data)

            # Check for pagination
            next_page = response.headers.get("X-Next-Page")
            url = f"{self.api_url}{endpoint}?page={next_page}" if next_page else None

        return all_data

    async def fetch_groups(self, parent_id: Optional[int] = None) -> List[Dict[str, Any]]:
        """Fetch groups and subgroups from GitLab."""
        logging.info(f"Fetching groups from GitLab... Parent ID: {parent_id}")
        endpoint = "/groups" if not parent_id else f"/groups/{parent_id}/subgroups"
        data = await self._rate_limited_request(endpoint)
        all_groups = []

        for group in data:
            all_groups.append({
                "identifier": group["id"],
                "name": group["name"],
                "url": group["web_url"],
                "description": group.get("description"),
                "visibility": group.get("visibility"),
            })

            # Recursively fetch subgroups, only if there are any
            if group.get("subgroup_count", 0) > 0:
                subgroups = await self.fetch_groups(parent_id=group["id"])
                all_groups.extend(subgroups)

        return all_groups


    async def fetch_projects(self) -> List[Dict[str, Any]]:
        """Fetch projects from GitLab."""
        logging.info("Fetching projects from GitLab...")
        endpoint = "/projects"
        params = {"per_page": self.rate_limit}
        data = await self._rate_limited_request(endpoint, params)

        return [
            {
                "identifier": project["id"],
                "name": project["name"],
                "url": project["web_url"],
                "description": project["description"],
                "namespace": project.get("namespace", {}).get("full_path"),
            }
            for project in data
        ]

    async def fetch_merge_requests(self) -> List[Dict[str, Any]]:
        """Fetch merge requests from GitLab."""
        logging.info("Fetching merge requests from GitLab...")
        endpoint = "/merge_requests"
        params = {"scope": "all", "per_page": self.rate_limit}
        data = await self._rate_limited_request(endpoint, params)

        return [
            {
                "identifier": mr["id"],
                "title": mr["title"],
                "status": mr["state"],
                "author": mr["author"]["username"],
                "createdAt": mr["created_at"],
                "updatedAt": mr["updated_at"],
                "mergedAt": mr.get("merged_at"),
                "link": mr["web_url"],
                "reviewers": [reviewer["username"] for reviewer in mr.get("reviewers", [])],
            }
            for mr in data
        ]

    async def fetch_issues(self) -> List[Dict[str, Any]]:
        """Fetch issues from GitLab."""
        logging.info("Fetching issues from GitLab...")
        endpoint = "/issues"
        params = {"scope": "all", "per_page": self.rate_limit}
        data = await self._rate_limited_request(endpoint, params)

        return [
            {
                "identifier": issue["id"],
                "title": issue["title"],
                "status": issue["state"],
                "author": issue["author"]["username"],
                "createdAt": issue["created_at"],
                "updatedAt": issue["updated_at"],
                "closedAt": issue.get("closed_at"),
                "link": issue["web_url"],
                "labels": issue.get("labels", []),
            }
            for issue in data
        ]

    async def setup_webhook(self) -> None:
        """Set up an instance-level webhook to handle events across all GitLab projects and groups."""
        logging.info("Setting up instance-level webhook for all GitLab resources...")
        endpoint = "/hooks"
        payload = {
            "url": WEBHOOK_URL,
            "enable_ssl_verification": True,
            "push_events": True,
            "merge_requests_events": True,
            "issues_events": True,
            "note_events": True,
            "tag_push_events": True,
            "wiki_page_events": True,
            "pipeline_events": True,
            "job_events": True,
            "deployment_events": True
        }
        headers = {"Authorization": f"Bearer {self.token}"}

        try:
            response = await http_async_client.post(f"{self.api_url}{endpoint}", json=payload, headers=headers, timeout=30)
            response.raise_for_status()
            logging.info("Instance-level webhook setup complete.")
        except Exception as e:
            logging.error(f"Failed to set up instance-level webhook: {e}")