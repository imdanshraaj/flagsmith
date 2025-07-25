# Generated by Django 4.2.21 on 2025-06-16 16:55

import django.contrib.postgres.fields.hstore
from django.contrib.postgres.operations import HStoreExtension

from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ("app_analytics", "0005_featureevaluationraw_created_at_idx"),
    ]

    operations = [
        HStoreExtension(),
        migrations.AddField(
            model_name="apiusagebucket",
            name="labels",
            field=django.contrib.postgres.fields.hstore.HStoreField(default=dict),
        ),
        migrations.AddField(
            model_name="apiusageraw",
            name="labels",
            field=django.contrib.postgres.fields.hstore.HStoreField(default=dict),
        ),
        migrations.AddField(
            model_name="featureevaluationbucket",
            name="labels",
            field=django.contrib.postgres.fields.hstore.HStoreField(default=dict),
        ),
        migrations.AddField(
            model_name="featureevaluationraw",
            name="labels",
            field=django.contrib.postgres.fields.hstore.HStoreField(default=dict),
        ),
    ]
