from datetime import datetime, timedelta

import pytest
from django.utils import timezone
from freezegun.api import FrozenDateTimeFactory
from pytest_django.fixtures import SettingsWrapper
from pytest_mock import MockerFixture

from app_analytics.models import (
    APIUsageBucket,
    APIUsageRaw,
    FeatureEvaluationBucket,
    FeatureEvaluationRaw,
    Resource,
)
from app_analytics.tasks import (
    clean_up_old_analytics_data,
    populate_api_usage_bucket,
    populate_feature_evaluation_bucket,
    track_feature_evaluations_by_environment,
    track_request,
)
from app_analytics.types import TrackFeatureEvaluationsByEnvironmentData
from environments.models import Environment

pytestmark = pytest.mark.use_analytics_db


def _create_api_usage_event(environment_id: int, when: datetime) -> APIUsageRaw:
    event = APIUsageRaw.objects.create(
        environment_id=environment_id,
        host="host1",
        resource=Resource.FLAGS,
    )
    # update created_at
    event.created_at = when
    event.save()

    return event


@pytest.mark.freeze_time("2023-01-19T09:09:47.325132+00:00")
@pytest.mark.use_analytics_db
def test_populate_api_usage_bucket_multiple_runs(
    freezer: FrozenDateTimeFactory,
) -> None:
    # Given
    environment_id = 1
    bucket_size = 15
    now = timezone.now()
    # let's create events at every 1 minutes
    # for the last two hours, i.e: from 9:09:47 to 7:10:47
    for i in range(60 * 2):
        _create_api_usage_event(environment_id, now - timedelta(minutes=1 * i))
        # create events in some other environments as well - just to make sure
        # we don't aggregate them in the same environment
        _create_api_usage_event(999, now - timedelta(minutes=1 * i))

    # Next, let's go 1 hr back in the past and run this
    freezer.move_to(timezone.now() - timedelta(hours=1))
    populate_api_usage_bucket(bucket_size, run_every=60)

    # Then - we should have four buckets
    buckets = (
        APIUsageBucket.objects.filter(bucket_size=15, environment_id=environment_id)
        .order_by("created_at")
        .all()
    )

    assert len(buckets) == 4
    assert buckets[0].created_at.isoformat() == "2023-01-19T07:00:00+00:00"
    # since we started at 7:10:47, the first bucket should have 5 events
    assert buckets[0].total_count == 5

    # the rest of them should have 15 events
    assert buckets[1].created_at.isoformat() == "2023-01-19T07:15:00+00:00"
    assert buckets[1].total_count == 15

    assert buckets[2].created_at.isoformat() == "2023-01-19T07:30:00+00:00"
    assert buckets[2].total_count == 15

    assert buckets[3].created_at.isoformat() == "2023-01-19T07:45:00+00:00"
    assert buckets[3].total_count == 15

    # Now, let's move forward 1hr and run this again
    freezer.move_to(timezone.now() + timedelta(hours=1))
    populate_api_usage_bucket(bucket_size, run_every=60)

    # Then - we should have another four buckets created by the second run
    buckets = (
        APIUsageBucket.objects.filter(bucket_size=15, environment_id=environment_id)
        .order_by("created_at")
        .all()
    )
    assert len(buckets) == 8

    # with 15 events each
    assert buckets[4].created_at.isoformat() == "2023-01-19T08:00:00+00:00"
    assert buckets[4].total_count == 15

    assert buckets[5].created_at.isoformat() == "2023-01-19T08:15:00+00:00"
    assert buckets[5].total_count == 15

    assert buckets[6].created_at.isoformat() == "2023-01-19T08:30:00+00:00"
    assert buckets[6].total_count == 15

    assert buckets[7].created_at.isoformat() == "2023-01-19T08:45:00+00:00"
    assert buckets[7].total_count == 15


@pytest.mark.parametrize(
    "bucket_size, runs_every",
    [(15, 60), (10, 60), (10, 30), (30, 30), (60, 60), (10, 10), (60, 60 * 4)],
)
@pytest.mark.freeze_time("2023-01-19T09:09:47.325132+00:00")
@pytest.mark.use_analytics_db
def test_populate_api_usage_bucket(
    freezer: FrozenDateTimeFactory,
    bucket_size: int,
    runs_every: int,
) -> None:
    # Given
    environment_id = 1
    now = timezone.now()
    # let's create events at every 1 minutes
    # for the last two hours, i.e: from 9:09:47
    for i in range(runs_every * 2):
        _create_api_usage_event(environment_id, now - timedelta(minutes=1 * i))
        # create events in some other environments as well - just to make sure
        # we don't aggregate them in the same environment
        _create_api_usage_event(999, now - timedelta(minutes=1 * i))

    # When
    populate_api_usage_bucket(bucket_size, run_every=runs_every)

    # Then
    buckets = (
        APIUsageBucket.objects.filter(
            bucket_size=bucket_size,
            total_count=bucket_size,
            environment_id=environment_id,
        )
        .order_by("-created_at")
        .all()
    )

    assert len(buckets) == runs_every // bucket_size
    # and
    start_time = timezone.now().replace(minute=0, second=0, microsecond=0)
    for bucket in buckets:
        start_time = start_time - timedelta(minutes=bucket_size)
        assert bucket.created_at == start_time
        assert bucket.total_count == bucket_size


@pytest.mark.use_analytics_db
def test_track_request__postgres__inserts_expected(
    settings: SettingsWrapper,
    environment: Environment,
) -> None:
    # Given
    settings.USE_POSTGRES_FOR_ANALYTICS = True
    host = "testserver"
    environment_key = environment.api_key
    resource = Resource.FLAGS

    # When
    track_request(resource, host, environment_key)

    # Then
    assert (
        APIUsageRaw.objects.filter(
            resource=resource, host=host, environment_id=environment.id
        ).count()
        == 1
    )


def test_track_request__influx__calls_expected(
    db: None,
    settings: SettingsWrapper,
    mocker: MockerFixture,
    environment: Environment,
) -> None:
    # Given
    settings.INFLUXDB_TOKEN = "test_token"
    track_request_influxdb_mock = mocker.patch(
        "app_analytics.tasks.track_request_influxdb",
        autospec=True,
    )
    host = "testserver"
    environment_key = environment.api_key
    resource = Resource.FLAGS

    # When
    track_request(resource, host, environment_key)

    # Then
    track_request_influxdb_mock.assert_called_once_with(
        resource=resource,
        host=host,
        environment=environment,
        count=1,
        labels={},
    )


@pytest.mark.use_analytics_db
def test_track_feature_evaluation(settings: SettingsWrapper) -> None:
    # Given
    settings.USE_POSTGRES_FOR_ANALYTICS = True
    environment_id = 1
    feature_evaluations = [
        TrackFeatureEvaluationsByEnvironmentData(
            feature_name="feature1",
            labels={},
            evaluation_count=10,
        ),
        TrackFeatureEvaluationsByEnvironmentData(
            feature_name="feature2",
            labels={},
            evaluation_count=20,
        ),
    ]

    # When
    track_feature_evaluations_by_environment(
        environment_id=environment_id,
        feature_evaluations=feature_evaluations,
    )

    # Then
    assert (
        FeatureEvaluationRaw.objects.filter(
            environment_id=environment_id, feature_name="feature1", evaluation_count=10
        ).count()
        == 1
    )
    assert (
        FeatureEvaluationRaw.objects.filter(
            environment_id=environment_id, feature_name="feature2", evaluation_count=20
        ).count()
        == 1
    )


@pytest.mark.use_analytics_db
def test_track_feature_evaluation__influx__calls_expected(
    settings: SettingsWrapper,
    mocker: MockerFixture,
) -> None:
    # Given
    settings.INFLUXDB_TOKEN = "test_token"
    track_feature_evaluation_influxdb_mock = mocker.patch(
        "app_analytics.tasks.track_feature_evaluation_influxdb",
        autospec=True,
    )
    environment_id = 1
    feature_evaluations = [
        (
            TrackFeatureEvaluationsByEnvironmentData(
                feature_name="feature1",
                labels={},
                evaluation_count=10,
            )
        ),
        (
            TrackFeatureEvaluationsByEnvironmentData(
                feature_name="feature2",
                labels={},
                evaluation_count=20,
            )
        ),
    ]

    # When
    track_feature_evaluations_by_environment(
        environment_id=environment_id,
        feature_evaluations=feature_evaluations,
    )

    # Then
    track_feature_evaluation_influxdb_mock.assert_called_once_with(
        environment_id=environment_id,
        feature_evaluations=feature_evaluations,
    )


@pytest.mark.freeze_time("2023-01-19T09:09:47.325132+00:00")
@pytest.mark.use_analytics_db
def test_populate_feature_evaluation_bucket_15m(freezer: FrozenDateTimeFactory) -> None:
    # Given
    environment_id = 1
    bucket_size = 15
    feature_name = "feature1"
    now = timezone.now()

    # let's create events at every 1 minutes
    # for the last two hours, i.e: from 9:09:47 to 7:10:47
    for i in range(60 * 2):
        _create_feature_evaluation_event(
            environment_id,
            feature_name,
            1,
            now - timedelta(minutes=1 * i),
        )
        # create events in some other environments
        _create_feature_evaluation_event(
            999,
            feature_name,
            1,
            now - timedelta(minutes=1 * i),
        )
        # create events for some other features
        _create_feature_evaluation_event(
            environment_id,
            "some_other_feature",
            1,
            now - timedelta(minutes=1 * i),
        )
    # Next, let's go 1 hr back in the past and run this
    freezer.move_to(timezone.now() - timedelta(hours=1))
    populate_feature_evaluation_bucket(bucket_size, run_every=60)

    # Then - we should have four buckets
    buckets = (
        FeatureEvaluationBucket.objects.filter(
            bucket_size=bucket_size,
            environment_id=environment_id,
            feature_name=feature_name,
        )
        .order_by("created_at")
        .all()
    )

    assert len(buckets) == 4
    assert buckets[0].created_at.isoformat() == "2023-01-19T07:00:00+00:00"
    # since we started at 7:10:47, the first bucket should have 5 events
    assert buckets[0].total_count == 5

    # the rest of them should have 15 events
    assert buckets[1].created_at.isoformat() == "2023-01-19T07:15:00+00:00"
    assert buckets[1].total_count == 15

    assert buckets[2].created_at.isoformat() == "2023-01-19T07:30:00+00:00"
    assert buckets[2].total_count == 15

    assert buckets[3].created_at.isoformat() == "2023-01-19T07:45:00+00:00"
    assert buckets[3].total_count == 15

    # Now, let's move forward 1hr and run this again
    freezer.move_to(timezone.now() + timedelta(hours=1))
    populate_feature_evaluation_bucket(bucket_size, run_every=60)

    # Then - we should have another four buckets created by the second run
    buckets = (
        FeatureEvaluationBucket.objects.filter(
            bucket_size=bucket_size,
            environment_id=environment_id,
            feature_name=feature_name,
        )
        .order_by("created_at")
        .all()
    )
    assert len(buckets) == 8

    # with 15 events each
    assert buckets[4].created_at.isoformat() == "2023-01-19T08:00:00+00:00"
    assert buckets[4].total_count == 15

    assert buckets[5].created_at.isoformat() == "2023-01-19T08:15:00+00:00"
    assert buckets[5].total_count == 15

    assert buckets[6].created_at.isoformat() == "2023-01-19T08:30:00+00:00"
    assert buckets[6].total_count == 15

    assert buckets[7].created_at.isoformat() == "2023-01-19T08:45:00+00:00"
    assert buckets[7].total_count == 15


@pytest.mark.freeze_time("2023-01-19T09:00:00+00:00")
@pytest.mark.use_analytics_db
def test_populate_feature_evaluation_bucket__upserts_buckets(
    freezer: FrozenDateTimeFactory,
) -> None:
    # Given
    environment_id = 1
    bucket_size = 15
    feature_name = "feature1"
    then = timezone.now()

    _create_feature_evaluation_event(environment_id, feature_name, 1, then)

    # move the time to 9:47
    freezer.move_to(timezone.now().replace(minute=47))

    # populate buckets to have an existing one
    populate_feature_evaluation_bucket(bucket_size=bucket_size, run_every=60)

    # add historical raw data
    _create_feature_evaluation_event(environment_id, feature_name, 1, then)

    # When
    # Feature usage is populated over existing buckets
    populate_feature_evaluation_bucket(bucket_size=bucket_size, run_every=60)

    # Then
    # Buckets are correctly set according to current raw data
    buckets = FeatureEvaluationBucket.objects.filter(
        environment_id=environment_id,
        bucket_size=bucket_size,
    ).all()
    assert len(buckets) == 1
    assert buckets[0].total_count == 2


@pytest.mark.freeze_time("2023-01-19T09:00:00+00:00")
@pytest.mark.use_analytics_db
def test_populate_feature_evaluation_bucket__source_bucket_size__returns_expected(
    freezer: FrozenDateTimeFactory,
) -> None:
    # Given
    environment_id = 1
    feature_name = "feature1"

    # let's create 3, 5m buckets
    now = timezone.now()
    for _ in range(3):
        FeatureEvaluationBucket.objects.create(
            environment_id=environment_id,
            feature_name=feature_name,
            total_count=100,
            created_at=now,
            bucket_size=5,
        )
        now = now - timedelta(minutes=5)

    # create one more bucket with a different size
    FeatureEvaluationBucket.objects.create(
        environment_id=environment_id,
        feature_name=feature_name,
        total_count=100,
        created_at=now,
        bucket_size=10,
    )

    # move the time to 9:47
    freezer.move_to(timezone.now().replace(minute=47))

    # When
    populate_feature_evaluation_bucket(
        bucket_size=15, run_every=60, source_bucket_size=5
    )

    # Then
    assert (
        FeatureEvaluationBucket.objects.filter(
            bucket_size=15,
            total_count=300,
            environment_id=environment_id,
            feature_name=feature_name,
        ).count()
        == 1
    )


@pytest.mark.freeze_time("2023-01-19T09:00:00+00:00")
@pytest.mark.use_analytics_db
def test_populate_api_usage_bucket__upserts_buckets(
    freezer: FrozenDateTimeFactory,
) -> None:
    # Given
    environment_id = 1
    bucket_size = 15

    then = timezone.now()

    _create_api_usage_event(environment_id, then)

    # move the time to 9:47
    freezer.move_to(timezone.now().replace(minute=47))

    # populate buckets to have an existing one
    populate_api_usage_bucket(bucket_size=bucket_size, run_every=60)

    # add historical raw data
    _create_api_usage_event(environment_id, then)

    # When
    # API usage is populated over existing buckets
    populate_api_usage_bucket(bucket_size=bucket_size, run_every=60)

    # Then
    # Buckets are correctly set according to current raw data
    buckets = APIUsageBucket.objects.filter(
        environment_id=environment_id,
        bucket_size=bucket_size,
    ).all()
    assert len(buckets) == 1
    assert buckets[0].total_count == 2


@pytest.mark.freeze_time("2023-01-19T09:00:00+00:00")
@pytest.mark.use_analytics_db
def test_populate_api_usage_bucket_using_a_bucket(
    freezer: FrozenDateTimeFactory,
) -> None:
    # Given
    environment_id = 1

    # let's create 3, 5m buckets
    now = timezone.now()
    for _ in range(3):
        APIUsageBucket.objects.create(
            environment_id=environment_id,
            resource=Resource.FLAGS,
            total_count=100,
            created_at=now,
            bucket_size=5,
        )
        now = now - timedelta(minutes=5)

    # move the time to 9:47
    freezer.move_to(timezone.now().replace(minute=47))

    # When
    populate_api_usage_bucket(bucket_size=15, run_every=60, source_bucket_size=5)

    # Then
    assert APIUsageBucket.objects.filter(bucket_size=15, total_count=300).count() == 1


def _create_feature_evaluation_event(
    environment_id: int,
    feature_name: str,
    count: int,
    when: datetime,
) -> FeatureEvaluationRaw:
    event = FeatureEvaluationRaw.objects.create(
        environment_id=environment_id,
        feature_name=feature_name,
        evaluation_count=count,
    )
    # update created_at
    event.created_at = when
    event.save()

    return event


@pytest.mark.use_analytics_db
def test_clean_up_old_analytics_data_does_nothing_if_no_data() -> None:
    # When
    clean_up_old_analytics_data()

    # Then
    # no exception was raised


@pytest.mark.use_analytics_db
def test_clean_up_old_analytics_data_removes_old_data(
    settings: SettingsWrapper,
) -> None:
    # Given
    now = timezone.now()
    settings.RAW_ANALYTICS_DATA_RETENTION_DAYS = 2
    settings.BUCKETED_ANALYTICS_DATA_RETENTION_DAYS = 4

    environment_id = 1

    # APIUsageRaw data that should not be removed
    new_api_usage_raw_data = []
    new_api_usage_raw_data.append(_create_api_usage_event(environment_id, now))
    new_api_usage_raw_data.append(
        _create_api_usage_event(environment_id, now - timedelta(days=1))
    )

    # APIUsageRaw data that should be removed
    _create_api_usage_event(environment_id, now - timedelta(days=2))
    _create_api_usage_event(environment_id, now - timedelta(days=3))

    # APIUsageBucket data that should not be removed
    new_api_usage_bucket = APIUsageBucket.objects.create(
        environment_id=environment_id,
        resource=Resource.FLAGS,
        total_count=100,
        created_at=now,
        bucket_size=5,
    )
    # APIUsageBucket data that should be removed
    APIUsageBucket.objects.create(
        environment_id=environment_id,
        resource=Resource.FLAGS,
        total_count=100,
        created_at=now - timedelta(days=5),
        bucket_size=5,
    )

    # FeatureEvaluationRaw data that should not be removed
    new_feature_evaluation_raw_data = []
    new_feature_evaluation_raw_data.append(
        _create_feature_evaluation_event(environment_id, "feature1", 1, now)
    )
    new_feature_evaluation_raw_data.append(
        _create_feature_evaluation_event(
            environment_id,
            "feature1",
            1,
            now - timedelta(days=1),
        )
    )

    # FeatureEvaluationRaw data that should be removed
    _create_feature_evaluation_event(
        environment_id,
        "feature1",
        1,
        now - timedelta(days=3),
    )
    _create_feature_evaluation_event(
        environment_id,
        "feature1",
        1,
        now - timedelta(days=2),
    )

    # FeatureEvaluationBucket data that should not be removed
    new_feature_evaluation_bucket = FeatureEvaluationBucket.objects.create(
        environment_id=environment_id,
        feature_name="feature1",
        total_count=100,
        created_at=now,
        bucket_size=5,
    )

    # FeatureEvaluationBucket data that should be removed
    FeatureEvaluationBucket.objects.create(
        environment_id=environment_id,
        feature_name="feature1",
        total_count=100,
        created_at=now - timedelta(days=5),
        bucket_size=5,
    )
    # When
    clean_up_old_analytics_data()

    # Then
    assert list(APIUsageRaw.objects.all()) == new_api_usage_raw_data
    assert list(FeatureEvaluationRaw.objects.all()) == new_feature_evaluation_raw_data
    assert list(FeatureEvaluationBucket.objects.all()) == [
        new_feature_evaluation_bucket
    ]
    assert list(APIUsageBucket.objects.all()) == [new_api_usage_bucket]
