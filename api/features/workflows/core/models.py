import importlib
import logging
import typing
from datetime import datetime

from django.conf import settings
from django.core.mail import send_mail
from django.db import models
from django.template.loader import render_to_string
from django.utils import timezone
from django_lifecycle import (  # type: ignore[import-untyped]
    AFTER_CREATE,
    AFTER_SAVE,
    AFTER_UPDATE,
    BEFORE_CREATE,
    BEFORE_DELETE,
    LifecycleModel,
    LifecycleModelMixin,
    hook,
)

from audit.constants import (
    CHANGE_REQUEST_APPROVED_MESSAGE,
    CHANGE_REQUEST_COMMITTED_MESSAGE,
    CHANGE_REQUEST_CREATED_MESSAGE,
    CHANGE_REQUEST_DELETED_MESSAGE,
)
from audit.related_object_type import RelatedObjectType
from audit.tasks import (
    create_feature_state_updated_by_change_request_audit_log,
    create_feature_state_went_live_audit_log,
)
from core.helpers import get_current_site_url
from core.models import (
    AbstractBaseExportableModel,
    SoftDeleteExportableModel,
    abstract_base_auditable_model_factory,
)
from core.workflows_services import ChangeRequestCommitService
from features.workflows.core.exceptions import (
    CannotApproveOwnChangeRequest,
    ChangeRequestDeletionError,
)

if typing.TYPE_CHECKING:
    from environments.models import Environment
    from projects.models import Project
    from users.models import FFAdminUser

logger = logging.getLogger(__name__)


class ChangeRequest(  # type: ignore[django-manager-missing]
    LifecycleModelMixin,  # type: ignore[misc]
    SoftDeleteExportableModel,
    abstract_base_auditable_model_factory(["uuid"]),  # type: ignore[misc]
):
    related_object_type = RelatedObjectType.CHANGE_REQUEST
    history_record_class_path = "features.workflows.core.models.HistoricalChangeRequest"

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    title = models.CharField(max_length=500)
    description = models.TextField(blank=True, null=True)

    # We allow null here so that deleting users does not cascade to deleting change
    # requests which can be used for historical purposes.
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        related_name="change_requests",
        null=True,
    )

    project = models.ForeignKey(
        "projects.Project",
        on_delete=models.CASCADE,
        related_name="change_requests",
        null=False,
    )

    environment = models.ForeignKey(
        "environments.Environment",
        on_delete=models.CASCADE,
        related_name="change_requests",
        null=True,
    )

    committed_at = models.DateTimeField(null=True)
    committed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        related_name="committed_change_requests",
        null=True,
    )

    ignore_conflicts = models.BooleanField(default=False)

    class Meta:
        # Explicit ordering to prevent pagination warnings.
        ordering = ("id",)

    def approve(self, user: "FFAdminUser"):  # type: ignore[no-untyped-def]
        if user.id == self.user_id:
            raise CannotApproveOwnChangeRequest(
                "User cannot approve their own Change Request."
            )

        ChangeRequestApproval.objects.update_or_create(
            change_request=self, user=user, defaults={"approved_at": timezone.now()}
        )

    def commit(self, committed_by: "FFAdminUser"):  # type: ignore[no-untyped-def]
        ChangeRequestCommitService(self).commit(committed_by)

    def get_create_log_message(self, history_instance) -> typing.Optional[str]:  # type: ignore[no-untyped-def]
        return CHANGE_REQUEST_CREATED_MESSAGE % self.title

    def get_delete_log_message(self, history_instance) -> typing.Optional[str]:  # type: ignore[no-untyped-def]
        return CHANGE_REQUEST_DELETED_MESSAGE % self.title

    def get_update_log_message(self, history_instance) -> typing.Optional[str]:  # type: ignore[no-untyped-def,return]
        if (
            history_instance.prev_record
            and history_instance.prev_record.committed_at is None
            and self.committed_at is not None
        ):
            return CHANGE_REQUEST_COMMITTED_MESSAGE % self.title

    def get_audit_log_author(self, history_instance) -> typing.Optional["FFAdminUser"]:  # type: ignore[no-untyped-def,return]  # noqa: E501  # noqa: E501
        if history_instance.history_type == "+":
            return self.user
        elif history_instance.history_type == "~" and (
            history_instance.prev_record
            and history_instance.prev_record.committed_at is None
            and self.committed_at is not None
        ):
            return self.committed_by

    def _get_environment(self) -> typing.Optional["Environment"]:
        return self.environment

    def _get_project(self) -> "Project":
        return self.project

    def is_approved(self) -> bool:
        if self.environment:
            return self.is_approved_via_environment()
        return self.is_approved_via_project()

    def is_approved_via_project(self) -> bool:
        return self.project.minimum_change_request_approvals is None or (
            self.approvals.filter(approved_at__isnull=False).count()
            >= self.project.minimum_change_request_approvals
        )

    def is_approved_via_environment(self) -> bool:
        return (
            self.environment.minimum_change_request_approvals is None  # type: ignore[union-attr]
            or (
                self.approvals.filter(approved_at__isnull=False).count()
                >= self.environment.minimum_change_request_approvals  # type: ignore[union-attr]
            )
        )

    @property
    def is_committed(self):  # type: ignore[no-untyped-def]
        return self.committed_at is not None

    @property
    def url(self):  # type: ignore[no-untyped-def]
        if not self.id:
            raise AttributeError(
                "Change request must be saved before it has a url attribute."
            )
        url = get_current_site_url()
        if self.environment:
            url += f"/project/{self.environment.project_id}"
            url += f"/environment/{self.environment.api_key}"
        else:
            url += f"/project/{self.project_id}"
        url += f"/change-requests/{self.id}"
        return url

    @property
    def email_subject(self):  # type: ignore[no-untyped-def]
        return f"Flagsmith Change Request: {self.title} (#{self.id})"

    @hook(BEFORE_CREATE, when="project", is_now=None)
    def set_project_from_environment(self):  # type: ignore[no-untyped-def]
        self.project_id = self.environment.project_id  # type: ignore[union-attr]

    @hook(AFTER_CREATE, when="committed_at", is_not=None)
    @hook(AFTER_SAVE, when="committed_at", is_not=None)
    def create_audit_log_for_related_feature_state(self):  # type: ignore[no-untyped-def]
        for feature_state in self.feature_states.all():
            if self.committed_at < feature_state.live_from:  # type: ignore[operator]
                create_feature_state_went_live_audit_log.delay(
                    args=(feature_state.id,),
                    delay_until=feature_state.live_from,
                )
            else:
                create_feature_state_updated_by_change_request_audit_log.delay(
                    args=(feature_state.id,),
                )

    @hook(BEFORE_DELETE)  # type: ignore[misc]
    def prevent_change_request_delete_if_committed(self) -> None:
        # In the workflows-logic module, we prevent change requests from being
        # deleted but, since this can have unexpected effects on published
        # feature states, we also want to prevent it at the ORM level.
        if self.committed_at and not (
            (self.environment and self.environment.deleted_at)
            or (self.project and self.project.deleted_at)
            or (self.live_from and self.live_from > timezone.now())
        ):
            raise ChangeRequestDeletionError(
                "Cannot delete a Change Request that has been committed."
            )

    @property
    def live_from(self) -> datetime | None:
        # Note: a change request can only have one of either
        # feature_states, change_sets or environment_feature_versions

        # First we check if there are feature states associated with the change request
        # and, if so, we return the live_from of the feature state with the earliest
        # live_from.
        if first_feature_state := self.feature_states.order_by("live_from").first():
            return first_feature_state.live_from

        # Then we check the change sets.
        elif first_change_set := self.change_sets.order_by("live_from").first():
            return first_change_set.live_from  # type: ignore[no-any-return]

        # Finally, we do the same for environment feature versions.
        elif (
            first_environment_feature_version
            := self.environment_feature_versions.order_by("live_from").first()
        ):
            return first_environment_feature_version.live_from

        return None


class ChangeRequestApproval(LifecycleModel, abstract_base_auditable_model_factory()):  # type: ignore[misc]
    related_object_type = RelatedObjectType.CHANGE_REQUEST
    history_record_class_path = (
        "features.workflows.core.models.HistoricalChangeRequestApproval"
    )

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)  # type: ignore[var-annotated]
    change_request = models.ForeignKey(  # type: ignore[var-annotated]
        ChangeRequest, on_delete=models.CASCADE, related_name="approvals"
    )
    created_at = models.DateTimeField(auto_now_add=True)  # type: ignore[var-annotated]
    approved_at = models.DateTimeField(null=True)  # type: ignore[var-annotated]

    class Meta:
        unique_together = ("user", "change_request")

    @hook(AFTER_CREATE, when="approved_at", is_now=None)
    def send_email_notification_to_assignee(self):  # type: ignore[no-untyped-def]
        context = {
            "url": self.change_request.url,
            "approver": self.user,
            "author": self.change_request.user,
        }

        send_mail(
            subject=self.change_request.email_subject,
            message=render_to_string(
                "workflows_core/change_request_assignee_notification.txt", context
            ),
            from_email=settings.DEFAULT_FROM_EMAIL,
            recipient_list=[self.user.email],
            html_message=render_to_string(
                "workflows_core/change_request_assignee_notification.html", context
            ),
            fail_silently=True,
        )

    @hook(AFTER_CREATE, when="approved_at", is_not=None)
    @hook(AFTER_UPDATE, when="approved_at", was=None, is_not=None)
    def send_email_notification_to_author(self):  # type: ignore[no-untyped-def]
        context = {
            "url": self.change_request.url,
            "approver": self.user,
            "author": self.change_request.user,
        }

        send_mail(
            subject=self.change_request.email_subject,
            message=render_to_string(
                "workflows_core/change_request_approved_author_notification.txt",
                context,
            ),
            from_email=settings.DEFAULT_FROM_EMAIL,
            recipient_list=[self.change_request.user.email],
            html_message=render_to_string(
                "workflows_core/change_request_approved_author_notification.html",
                context,
            ),
            fail_silently=True,
        )

    def get_create_log_message(self, history_instance) -> typing.Optional[str]:  # type: ignore[no-untyped-def,return]
        if self.approved_at is not None:
            return CHANGE_REQUEST_APPROVED_MESSAGE % self.change_request.title  # type: ignore[no-any-return]

    def get_update_log_message(self, history_instance) -> typing.Optional[str]:  # type: ignore[no-untyped-def,return]
        if (
            history_instance.prev_record.approved_at is None
            and self.approved_at is not None
        ):
            return CHANGE_REQUEST_APPROVED_MESSAGE % self.change_request.title  # type: ignore[no-any-return]

    def get_audit_log_related_object_id(self, history_instance) -> int:  # type: ignore[no-untyped-def]
        return self.change_request_id  # type: ignore[no-any-return]

    def get_audit_log_author(self, history_instance) -> "FFAdminUser":  # type: ignore[no-untyped-def]
        return self.user  # type: ignore[no-any-return]

    def _get_environment(self):  # type: ignore[no-untyped-def]
        return self.change_request.environment

    def _get_project(self):  # type: ignore[no-untyped-def]
        return self.change_request._get_project()


class ChangeRequestGroupAssignment(AbstractBaseExportableModel, LifecycleModel):  # type: ignore[misc]
    change_request = models.ForeignKey(
        ChangeRequest, on_delete=models.CASCADE, related_name="group_assignments"
    )
    group = models.ForeignKey("users.UserPermissionGroup", on_delete=models.CASCADE)

    @hook(AFTER_SAVE)
    def notify_group(self):  # type: ignore[no-untyped-def]
        if settings.WORKFLOWS_LOGIC_INSTALLED:
            workflows_tasks = importlib.import_module("workflows_logic.tasks")
            workflows_tasks.notify_group_of_change_request_assignment.delay(
                kwargs={"change_request_group_assignment_id": self.id}
            )
