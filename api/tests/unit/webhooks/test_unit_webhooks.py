import hashlib
import hmac
import json
from typing import Callable, Type
from unittest import mock
from unittest.mock import MagicMock

import pytest
import requests
import responses
from django.core.serializers.json import DjangoJSONEncoder
from django.urls import reverse
from pytest_django.fixtures import SettingsWrapper
from pytest_mock import MockerFixture
from requests.exceptions import ConnectionError, Timeout
from rest_framework import status
from rest_framework.test import APIClient

from core.constants import FLAGSMITH_SIGNATURE_HEADER
from core.signing import sign_payload
from environments.models import Environment, Webhook
from organisations.models import Organisation, OrganisationWebhook
from webhooks.webhooks import (
    WebhookEventType,
    WebhookType,
    call_environment_webhooks,
    call_integration_webhook,
    call_organisation_webhooks,
    call_webhook_with_failure_mail_after_retries,
    generate_environment_sample_webhook_data,
)


@mock.patch("webhooks.webhooks.requests")
def test_webhooks_requests_made_to_all_urls_for_environment(
    mock_requests: MagicMock,
    environment: Environment,
) -> None:
    # Given
    webhook_1 = Webhook.objects.create(
        url="http://url.1.com", enabled=True, environment=environment
    )
    webhook_2 = Webhook.objects.create(
        url="http://url.2.com", enabled=True, environment=environment
    )

    # When
    call_environment_webhooks(
        environment_id=environment.id,
        data={},
        event_type=WebhookEventType.FLAG_UPDATED.value,
    )

    # Then
    assert len(mock_requests.post.call_args_list) == 2

    # and
    call_1_args, _ = mock_requests.post.call_args_list[0]
    call_2_args, _ = mock_requests.post.call_args_list[1]
    all_call_args = call_1_args + call_2_args
    assert all(str(webhook.url) in all_call_args for webhook in (webhook_1, webhook_2))


@mock.patch("webhooks.webhooks.requests")
def test_webhooks_request_not_made_to_disabled_webhook(
    mock_requests: MagicMock,
    environment: Environment,
) -> None:
    # Given
    Webhook.objects.create(
        url="http://url.1.com", enabled=False, environment=environment
    )

    # When
    call_environment_webhooks(
        environment_id=environment.id,
        data={},
        event_type=WebhookEventType.FLAG_UPDATED.value,
    )

    # Then
    mock_requests.post.assert_not_called()


@mock.patch("webhooks.webhooks.WebhookSerializer")
@mock.patch("webhooks.webhooks.requests")
def test_request_made_with_correct_signature(
    mock_requests: MagicMock,
    webhook_serializer: MagicMock,
    environment: Environment,
) -> None:
    # Given
    payload = {"key": "value"}
    webhook_serializer.return_value.data = payload
    secret = "random_key"
    Webhook.objects.create(
        url="http://url.1.com",
        enabled=True,
        environment=environment,
        secret=secret,
    )

    expected_signature = hmac.new(
        key=secret.encode(),
        msg=json.dumps(payload).encode(),
        digestmod=hashlib.sha256,
    ).hexdigest()

    call_environment_webhooks(
        environment_id=environment.id,
        data={},
        event_type=WebhookEventType.FLAG_UPDATED.value,
    )
    # When
    _, kwargs = mock_requests.post.call_args_list[0]
    # Then
    received_signature = kwargs["headers"][FLAGSMITH_SIGNATURE_HEADER]
    assert hmac.compare_digest(expected_signature, received_signature) is True


@mock.patch("webhooks.webhooks.requests")
def test_request_does_not_have_signature_header_if_secret_is_not_set(
    mock_requests: MagicMock,
    environment: Environment,
) -> None:
    # Given
    Webhook.objects.create(
        url="http://url.1.com", enabled=True, environment=environment
    )
    # When
    call_environment_webhooks(
        environment_id=environment.id,
        data={},
        event_type=WebhookEventType.FLAG_UPDATED.value,
    )

    # Then
    _, kwargs = mock_requests.post.call_args_list[0]
    assert FLAGSMITH_SIGNATURE_HEADER not in kwargs["headers"]


@pytest.mark.parametrize("expected_error", [ConnectionError, Timeout])
@pytest.mark.django_db
def test_call_environment_webhooks__multiple_webhooks__failure__calls_expected(
    mocker: MockerFixture,
    expected_error: Type[Exception],
    environment: Environment,
) -> None:
    # Given
    requests_post_mock = mocker.patch("webhooks.webhooks.requests.post")
    requests_post_mock.side_effect = expected_error()
    send_failure_email_mock: mock.Mock = mocker.patch(
        "webhooks.webhooks.send_failure_email"
    )

    webhook_1 = Webhook.objects.create(
        url="http://url.1.com",
        enabled=True,
        environment=environment,
    )
    webhook_2 = Webhook.objects.create(
        url="http://url.2.com",
        enabled=True,
        environment=environment,
    )

    expected_data = {}  # type: ignore[var-annotated]
    expected_event_type = WebhookEventType.FLAG_UPDATED.value
    expected_send_failure_email_data = {
        "event_type": expected_event_type,
        "data": expected_data,
    }
    expected_send_failure_status_code = f"N/A ({expected_error.__name__})"

    retries = 4
    # When
    call_environment_webhooks(
        environment_id=environment.id,
        data=expected_data,
        event_type=expected_event_type,
        retries=retries,
    )

    # Then
    assert requests_post_mock.call_count == 2 * retries
    assert send_failure_email_mock.call_count == 2
    send_failure_email_mock.assert_has_calls(
        [
            mocker.call(
                webhook_2,
                expected_send_failure_email_data,
                WebhookType.ENVIRONMENT.value,
                expected_send_failure_status_code,
            ),
            mocker.call(
                webhook_1,
                expected_send_failure_email_data,
                WebhookType.ENVIRONMENT.value,
                expected_send_failure_status_code,
            ),
        ],
        any_order=True,
    )


@pytest.mark.parametrize("expected_error", [ConnectionError, Timeout])
@pytest.mark.django_db
def test_call_organisation_webhooks__multiple_webhooks__failure__calls_expected(
    mocker: MockerFixture,
    expected_error: Type[Exception],
    organisation: Organisation,
    settings: SettingsWrapper,
) -> None:
    # Given
    requests_post_mock = mocker.patch("webhooks.webhooks.requests.post")
    requests_post_mock.side_effect = expected_error()
    send_failure_email_mock: mock.Mock = mocker.patch(
        "webhooks.webhooks.send_failure_email"
    )

    webhook_1 = OrganisationWebhook.objects.create(
        url="http://url.1.com", enabled=True, organisation=organisation
    )
    webhook_2 = OrganisationWebhook.objects.create(
        url="http://url.2.com", enabled=True, organisation=organisation
    )

    expected_data = {}  # type: ignore[var-annotated]
    expected_event_type = WebhookEventType.FLAG_UPDATED.value
    expected_send_failure_email_data = {
        "event_type": expected_event_type,
        "data": expected_data,
    }
    expected_send_failure_status_code = f"N/A ({expected_error.__name__})"

    retries = 5

    # When
    call_organisation_webhooks(
        organisation_id=organisation.id,
        data=expected_data,
        event_type=expected_event_type,
        retries=retries,
    )

    # Then
    assert requests_post_mock.call_count == 2 * retries
    assert send_failure_email_mock.call_count == 2
    send_failure_email_mock.assert_has_calls(
        [
            mocker.call(
                webhook_2,
                expected_send_failure_email_data,
                WebhookType.ORGANISATION.value,
                expected_send_failure_status_code,
            ),
            mocker.call(
                webhook_1,
                expected_send_failure_email_data,
                WebhookType.ORGANISATION.value,
                expected_send_failure_status_code,
            ),
        ],
        any_order=True,
    )


def test_call_webhook_with_failure_mail_after_retries_raises_error_on_invalid_args():  # type: ignore[no-untyped-def]
    try_count = 10
    with pytest.raises(ValueError):
        call_webhook_with_failure_mail_after_retries(0, {}, "", try_count=try_count)


def test_call_webhook_with_failure_mail_after_retries_does_not_retry_if_not_using_processor(  # type: ignore[no-untyped-def]  # noqa: E501
    mocker: MockerFixture, organisation: Organisation, settings: SettingsWrapper
):
    # Given
    requests_post_mock = mocker.patch("webhooks.webhooks.requests.post")
    requests_post_mock.side_effect = ConnectionError
    send_failure_email_mock: mock.Mock = mocker.patch(
        "webhooks.webhooks.send_failure_email"
    )

    settings.RETRY_WEBHOOKS = False

    webhook = OrganisationWebhook.objects.create(
        url="http://url.1.com", enabled=True, organisation=organisation
    )

    # When
    call_webhook_with_failure_mail_after_retries(
        webhook.id,
        data={},
        webhook_type=WebhookType.ORGANISATION.value,
        send_failure_mail=True,
    )

    # Then
    assert requests_post_mock.call_count == 1
    send_failure_email_mock.assert_called_once()


@responses.activate()
def test_call_integration_webhook_does_not_raise_error_on_backoff_give_up(
    mocker: MockerFixture,
) -> None:
    """
    This test is essentially verifying that the `raise_on_giveup` argument
    passed to the backoff decorator on _call_webhook is working as we
    expect it to.
    """
    # Given
    url = "https://test.com/webhook"
    config = mocker.MagicMock(secret=None, url=url)

    responses.add(url=url, method="POST", body=json.dumps({}), status=400)

    # When
    result = call_integration_webhook(config, data={})

    # Then
    # we don't get a result from the function (as expected), and no exception is
    # raised
    assert result is None


@pytest.mark.parametrize(
    "external_api_response_status, external_api_error_text, expected_final_status, expected_response_body",
    [
        (200, "", 200, {"detail": "Webhook test successful", "status": 200}),
        (
            400,
            "wrong-payload",
            400,
            {
                "detail": "Webhook returned invalid status",
                "status": 400,
                "body": "wrong-payload",
            },
        ),
        (
            401,
            "invalid-signature",
            400,
            {
                "detail": "Webhook returned invalid status",
                "status": 401,
                "body": "invalid-signature",
            },
        ),
        (
            500,
            "internal-server-error",
            400,
            {
                "detail": "Webhook returned invalid status",
                "status": 500,
                "body": "internal-server-error",
            },
        ),
    ],
)
def test_send_test_request_to_webhook_returns_correct_response(
    mocker: MockerFixture,
    admin_client: APIClient,
    external_api_response_status: int,
    expected_final_status: int,
    external_api_error_text: str,
    organisation: Organisation,
    expected_response_body: dict[str, str | int],
) -> None:
    # Given
    webhook_url = "http://test.webhook.com"
    mock_post = mocker.patch("requests.post")
    mock_response = MagicMock()
    mock_response.status_code = external_api_response_status
    mock_response.text = external_api_error_text
    mock_post.return_value = mock_response

    url = reverse("api-v1:webhooks:webhooks-test")

    data = {
        "webhook_url": webhook_url,
        "secret": "some-secret",
        "scope": {"type": "organisation", "id": organisation.id},
    }

    # When
    response = admin_client.post(
        url, data=json.dumps(data), content_type="application/json"
    )

    # Then
    assert response.status_code == expected_final_status
    mock_post.assert_called_once()
    assert response.json() == expected_response_body


@pytest.mark.parametrize(
    "secret, header_assertion",
    [
        (
            "some-secret",
            lambda headers, expected_signature: (
                FLAGSMITH_SIGNATURE_HEADER in headers
                and headers[FLAGSMITH_SIGNATURE_HEADER] == expected_signature
            ),
        ),
        (
            "some-other-secret",
            lambda headers, expected_signature: (
                FLAGSMITH_SIGNATURE_HEADER in headers
                and headers[FLAGSMITH_SIGNATURE_HEADER] == expected_signature
            ),
        ),
        (
            "",
            lambda headers, expected_signature: FLAGSMITH_SIGNATURE_HEADER
            not in headers,
        ),
    ],
)
def test_send_test_request_to_webhook_returns_has_correct_payload(
    mocker: MockerFixture,
    admin_client: APIClient,
    header_assertion: Callable[[dict[str, str], str], bool],
    environment: Environment,
    secret: str,
) -> None:
    # Given
    webhook_url = "http://test.webhook.com"
    mock_post = mocker.patch("requests.post")
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_post.return_value = mock_response

    url = reverse("api-v1:webhooks:webhooks-test")

    data = {
        "webhook_url": webhook_url,
        "secret": secret,
        "scope": {"type": "environment", "id": environment.api_key},
    }

    expected_signature = sign_payload(
        json.dumps(
            generate_environment_sample_webhook_data(),
            sort_keys=True,
            cls=DjangoJSONEncoder,
        ),
        secret,
    )
    # When
    response = admin_client.post(
        url, data=json.dumps(data), content_type="application/json"
    )

    # Then
    call_kwargs = mock_post.call_args.kwargs
    assert header_assertion(call_kwargs["headers"], expected_signature)
    assert response.status_code == 200


def test_send_test_request_to_webhook_handles_request_exception(
    mocker: MockerFixture,
    admin_client: APIClient,
    organisation: Organisation,
) -> None:
    # Given
    webhook_url = "http://test.webhook.com"
    mock_post = mocker.patch("requests.post")
    mock_post.side_effect = requests.exceptions.RequestException("Connection refused")

    url = reverse("api-v1:webhooks:webhooks-test")

    data = {
        "webhook_url": webhook_url,
        "secret": "some-secret",
        "scope": {"type": "organisation", "id": organisation.id},
    }

    # When
    response = admin_client.post(
        url, data=json.dumps(data), content_type="application/json"
    )

    # Then
    assert response.status_code == status.HTTP_400_BAD_REQUEST
    assert response.json() == {
        "detail": "Could not connect to webhook URL",
        "body": "Connection refused",
    }


def test_should_return_bad_request_when_webhook_url_is_missing(
    admin_client: APIClient,
    organisation: Organisation,
) -> None:
    # Given
    url = reverse("api-v1:webhooks:webhooks-test")
    data = {
        "webhook_url": "",
        "secret": "some-secret",
        "scope": {"type": "organisation", "id": organisation.id},
    }
    # When
    response = admin_client.post(
        url, data=json.dumps(data), content_type="application/json"
    )

    # Then
    assert response.status_code == status.HTTP_400_BAD_REQUEST
    assert response.json() == {"webhook_url": ["This field may not be blank."]}
