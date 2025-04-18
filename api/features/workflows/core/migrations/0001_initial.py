# Generated by Django 3.2.12 on 2022-03-25 14:58

from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion
import django_lifecycle.mixins  # type: ignore[import-untyped]


class Migration(migrations.Migration):

    initial = True

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ('environments', '0018_add_minimum_change_request_approvals_to_environment'),
    ]

    operations = [
        migrations.CreateModel(
            name='ChangeRequest',
            fields=[
                ('id', models.AutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('title', models.CharField(max_length=500)),
                ('description', models.TextField(blank=True, null=True)),
                ('deleted_at', models.DateTimeField(null=True)),
                ('committed_at', models.DateTimeField(null=True)),
                ('committed_by', models.ForeignKey(null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='committed_change_requests', to=settings.AUTH_USER_MODEL)),
                ('environment', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='change_requests', to='environments.environment')),
                ('user', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='change_requests', to=settings.AUTH_USER_MODEL)),
            ],
            options={
                'abstract': False,
            },
            bases=(django_lifecycle.mixins.LifecycleModelMixin, models.Model),
        ),
        migrations.CreateModel(
            name='ChangeRequestApproval',
            fields=[
                ('id', models.AutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('approved_at', models.DateTimeField(null=True)),
                ('change_request', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='approvals', to='workflows_core.changerequest')),
                ('user', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, to=settings.AUTH_USER_MODEL)),
            ],
            options={
                'unique_together': {('user', 'change_request')},
            },
        ),
    ]
