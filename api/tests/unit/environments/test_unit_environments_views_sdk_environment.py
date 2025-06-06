import time
from typing import TYPE_CHECKING

import pytest
from django.urls import reverse
from django.utils import timezone
from django.utils.http import http_date
from flag_engine.segments.constants import EQUAL
from rest_framework import status
from rest_framework.test import APIClient

from core.constants import FLAGSMITH_UPDATED_AT_HEADER
from environments.identities.models import Identity
from environments.models import Environment, EnvironmentAPIKey
from features.feature_types import MULTIVARIATE
from features.models import (  # type: ignore[attr-defined]
    STRING,
    Feature,
    FeatureSegment,
    FeatureState,
    FeatureStateValue,
)
from features.multivariate.models import MultivariateFeatureOption
from features.versioning.tasks import enable_v2_versioning
from projects.models import Project
from segments.models import Condition, Segment, SegmentRule
from segments.services import SegmentCloneService

if TYPE_CHECKING:
    from pytest_django import DjangoAssertNumQueries

    from organisations.models import Organisation


@pytest.mark.parametrize(
    "use_v2_feature_versioning, total_queries", [(True, 16), (False, 15)]
)
def test_get_environment_document(
    organisation_one: "Organisation",
    organisation_two: "Organisation",
    organisation_one_project_one: "Project",
    django_assert_num_queries: "DjangoAssertNumQueries",
    use_v2_feature_versioning: bool,
    total_queries: int,
) -> None:
    # Given
    project = organisation_one_project_one
    project2 = Project.objects.create(
        name="standin_project", organisation=organisation_two
    )

    environment = Environment.objects.create(
        name="Test Environment",
        project=project,
    )
    if use_v2_feature_versioning:
        enable_v2_versioning(environment.id)

    api_key = EnvironmentAPIKey.objects.create(environment=environment)
    client = APIClient()
    client.credentials(HTTP_X_ENVIRONMENT_KEY=api_key.key)

    # and some other sample data to make sure we're testing all of the document
    feature = Feature.objects.create(name="test_feature", project=project)
    for i in range(10):
        segment = Segment.objects.create(project=project)

        # Create a shallow clone which should not be returned in the document.
        SegmentCloneService(segment).shallow_clone(
            name=f"disregarded-clone-{i}",
            description=f"some-disregarded-clone-{i}",
            change_request=None,
        )

        # Create some other segments to ensure that the segments manager was
        # properly set.
        Segment.objects.create(
            project=project2,
            name=f"standin_segment{i}",
            description=f"Should not be selected {i}",
        )
        segment_rule = SegmentRule.objects.create(
            segment=segment, type=SegmentRule.ALL_RULE
        )
        Condition.objects.create(
            operator=EQUAL,
            property=f"property_{i}",
            value=f"value_{i}",
            rule=segment_rule,
        )
        nested_rule = SegmentRule.objects.create(
            segment=segment, rule=segment_rule, type=SegmentRule.ALL_RULE
        )
        Condition.objects.create(
            operator=EQUAL,
            property=f"nested_prop_{i}",
            value=f"nested_value_{i}",
            rule=nested_rule,
        )

        # Let's create segment override for each segment too
        feature_segment = FeatureSegment.objects.create(
            segment=segment, feature=feature, environment=environment
        )
        FeatureState.objects.create(
            feature=feature,
            environment=environment,
            feature_segment=feature_segment,
            enabled=True,
        )

        # Add identity overrides
        identity = Identity.objects.create(
            environment=environment,
            identifier=f"identity_{i}",
        )
        identity_feature_state = FeatureState.objects.create(
            identity=identity,
            feature=feature,
            environment=environment,
        )
        FeatureStateValue.objects.filter(feature_state=identity_feature_state).update(
            string_value="overridden",
            type=STRING,
        )

    for i in range(10):
        mv_feature = Feature.objects.create(
            name=f"mv_feature_{i}", project=project, type=MULTIVARIATE
        )
        MultivariateFeatureOption.objects.create(
            feature=mv_feature,
            default_percentage_allocation=10,
            string_value="option-1",
        )
        MultivariateFeatureOption.objects.create(
            feature=mv_feature,
            default_percentage_allocation=10,
            string_value="option-2",
        )

    # and the relevant URL to get an environment document
    url = reverse("api-v1:environment-document")

    # When
    with django_assert_num_queries(total_queries):
        response = client.get(url)

    # Then
    assert response.status_code == status.HTTP_200_OK
    assert response.json()
    assert len(response.data["project"]["segments"]) == 10
    assert response.headers[FLAGSMITH_UPDATED_AT_HEADER] == str(
        environment.updated_at.timestamp()
    )


def test_get_environment_document_fails_with_invalid_key(
    organisation_one: "Organisation",
    organisation_one_project_one: "Project",
) -> None:
    # Given
    project = organisation_one_project_one

    # an environment
    environment = Environment.objects.create(name="Test Environment", project=project)
    client = APIClient()

    # and we use the regular 'client' API key from the environment
    client.credentials(HTTP_X_ENVIRONMENT_KEY=environment.api_key)

    # and the relevant URL to get an environment document
    url = reverse("api-v1:environment-document")

    # When
    response = client.get(url)

    # Then
    # We get a 403 since only the server side API keys are able to access the
    # environment document
    assert response.status_code == status.HTTP_403_FORBIDDEN


def test_environment_document_if_modified_since(
    organisation_one: "Organisation",
    organisation_one_project_one: "Project",
) -> None:
    # Given
    project = organisation_one_project_one
    environment = Environment.objects.create(name="Test Environment", project=project)
    api_key = EnvironmentAPIKey.objects.create(environment=environment).key

    client = APIClient()
    client.credentials(HTTP_X_ENVIRONMENT_KEY=api_key)
    url = reverse("api-v1:environment-document")

    # When - first request
    response1 = client.get(url)

    # Then - first request should return 200 and include Last-Modified header
    assert response1.status_code == status.HTTP_200_OK
    last_modified = response1.headers["Last-Modified"]
    assert last_modified == http_date(environment.updated_at.timestamp())

    # When - second request with If-Modified-Since header
    client.credentials(
        HTTP_X_ENVIRONMENT_KEY=api_key,
        HTTP_IF_MODIFIED_SINCE=last_modified,
    )
    response2 = client.get(url)

    # Then - second request should return 304 Not Modified
    assert response2.status_code == status.HTTP_304_NOT_MODIFIED
    assert len(response2.content) == 0

    # sleep for 1s since If-Modified-Since is only accurate to the nearest second
    # https://developer.mozilla.org/en-US/docs/Web/HTTP/Reference/Headers/If-Modified-Since
    time.sleep(1)

    # When - environment is updated
    environment.updated_at = timezone.now()
    environment.save()

    # Then - request with same If-Modified-Since header should return 200
    response3 = client.get(url)
    assert response3.status_code == status.HTTP_200_OK
    assert response3.headers["Last-Modified"] == http_date(
        environment.updated_at.timestamp()
    )

    # When - request without If-Modified-Since header
    client.credentials(
        HTTP_X_ENVIRONMENT_KEY=api_key,
        HTTP_IF_MODIFIED_SINCE="",
    )
    response4 = client.get(url)

    # Then - actual environment is returned with a 200
    assert response4.status_code == status.HTTP_200_OK
    assert len(response4.content) > 0
