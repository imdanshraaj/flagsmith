# Generated by Django 3.2.23 on 2024-02-01 13:45

from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ('projects', '0021_add_identity_overrides_migration_status'),
        ('segments', '0019_add_audit_to_condition'),
    ]

    operations = [
        migrations.AlterField(
            model_name='segment',
            name='project',
            field=models.ForeignKey(on_delete=django.db.models.deletion.DO_NOTHING, related_name='segments', to='projects.project'),
        ),
    ]