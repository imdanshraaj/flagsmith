# Generated by Django 2.2.13 on 2020-06-07 10:57
import logging

from django.db import migrations

logger = logging.getLogger()
logger.setLevel(logging.INFO)


def migrate_feature_segments_forward(apps, schema_editor):  # type: ignore[no-untyped-def]
    FeatureSegment = apps.get_model('features', 'FeatureSegment')
    FeatureState = apps.get_model('features', 'FeatureState')

    # iterate over all current feature segments and ensure that one exists for all environments in it's project
    for feature_segment in FeatureSegment.objects.all():
        for idx, environment in enumerate(feature_segment.feature.project.environments.all()):
            # update the existing feature segment with the first environment and then create new feature segments
            # for the remaining environments
            if idx == 0:
                logger.info('Adding environment %d to feature segment %d' % (environment.id, feature_segment.id))
                feature_segment.environment = environment
                feature_segment.save()
            else:
                logger.info('Creating new feature segment for feature %d, environment %d and segment %d' % (
                    feature_segment.feature.id, environment.id, feature_segment.segment.id
                ))
                # create a copy of the feature segment by just setting the pk to None
                new_feature_segment = FeatureSegment.objects.create(
                    feature=feature_segment.feature,
                    environment=environment,
                    segment=feature_segment.segment,
                    priority=feature_segment.priority,
                    enabled=feature_segment.enabled,
                    value=feature_segment.value,
                    value_type=feature_segment.value_type,
                )

                # we now need to update the feature state to point to the correct feature segment
                FeatureState.objects.filter(
                    environment=environment, feature=new_feature_segment.feature, feature_segment=feature_segment
                ).update(feature_segment=new_feature_segment)

    assert not FeatureSegment.objects.filter(environment__isnull=True).exists()


def migrate_feature_segments_reverse(apps, schema_editor):  # type: ignore[no-untyped-def]
    """
    Reverse the above by making feature segments unique to a feature again.

    NOTE: THIS WILL RESULT IN A LOSS OF DATA!
    There is no way to determine which 'value' should be kept for a feature segment so we blindly just delete all but
    one of the feature segments. This has to be done due to the uniqueness constraint to ensure that we can still
    migrate backwards.
    """
    FeatureSegment = apps.get_model('features', 'FeatureSegment')
    Feature = apps.get_model('features', 'Feature')

    for feature in Feature.objects.filter(feature_segments__isnull=False).prefetch_related('feature_segments'):
        # todo: this is deleting more than it should. It should only be deleting one per feature / segment but it's
        #  ignoring cases where there are more than one segment
        first_feature_segment = feature.feature_segments.first()
        FeatureSegment.objects.filter(feature=feature).exclude(pk=first_feature_segment.pk).delete()


class Migration(migrations.Migration):

    dependencies = [
        ('features', '0017_auto_20200607_1005'),
    ]

    operations = [
        migrations.RunPython(
            migrate_feature_segments_forward, reverse_code=migrate_feature_segments_reverse
        ),
    ]
