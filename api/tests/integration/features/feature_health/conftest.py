import json

import pytest
from django.urls import reverse
from rest_framework.test import APIClient


def _get_feature_health_provider_webhook_url(
    project: int, api_client: APIClient, name: str
) -> str:
    feature_health_provider_data = {"name": name}
    url = reverse("api-v1:projects:feature-health-providers-list", args=[project])
    response = api_client.post(url, data=feature_health_provider_data)
    webhook_url: str = response.json()["webhook_url"]
    return webhook_url


@pytest.fixture
def sample_feature_health_provider_webhook_url(
    project: int, admin_client_new: APIClient
) -> str:
    return _get_feature_health_provider_webhook_url(
        project=project,
        api_client=admin_client_new,
        name="Sample",
    )


@pytest.fixture
def unhealthy_feature(
    sample_feature_health_provider_webhook_url: str,
    feature_name: str,
    feature: int,
    api_client: APIClient,
) -> int:
    api_client.post(
        sample_feature_health_provider_webhook_url,
        data=json.dumps({"feature": feature_name, "status": "unhealthy"}),
        content_type="application/json",
    )
    return feature


@pytest.fixture
def unhealthy_feature_health_event(
    project: int,
    unhealthy_feature: int,
    admin_client_new: APIClient,
) -> int:
    url = reverse(
        "api-v1:projects:feature-health-events-list",
        args=[project],
    )
    response = admin_client_new.get(url)
    unhealthy_feature_health_event_id: int = response.json()[0]["id"]
    return unhealthy_feature_health_event_id


@pytest.fixture
def grafana_feature_health_provider_webhook_url(
    project: int, admin_client_new: APIClient
) -> str:
    return _get_feature_health_provider_webhook_url(
        project=project,
        api_client=admin_client_new,
        name="Grafana",
    )
