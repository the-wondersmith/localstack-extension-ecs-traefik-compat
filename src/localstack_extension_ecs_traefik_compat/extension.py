from __future__ import annotations


import json
import time
import logging
from uuid import uuid4
from typing import Any, TYPE_CHECKING

from localstack.utils.container_utils.container_client import DockerContainerStatus
from localstack.extensions.api.extension import Extension, CompositeExceptionHandler
from localstack.services.stores import AccountRegionBundle
from localstack.utils.threads import FuncThread, start_worker_thread
from localstack.utils.docker_utils import DOCKER_CLIENT


if TYPE_CHECKING:
    from localstack.pro.core.services.ecs.models import ECSStore, Task as ECSTask  # noqa
    from localstack.pro.core.services.ecs.task_executors.docker import (
        TaskRunConfig,
        TaskContainer,
    )
else:
    ECSStore = ECSTask = TaskRunConfig = TaskContainer = Any


logger = logging.getLogger("l.ext.ecs-traefik-compat")


class EcsTraefikCompatExt(Extension):
    """An extension that fixes LocalStack's ECS compatibility issues with Traefik proxy."""

    # The namespace of all basic localstack extensions.
    namespace: str = "localstack.extensions"

    # The unique name of the extension set by the implementing class.
    name: str = "localstack-extension-ecs-traefik-compat"

    task: FuncThread | None = None

    def on_platform_ready(self):
        """Called when LocalStack is ready and the 'Ready' marker has been printed."""
        try:
            from localstack.pro.core.services.ecs.models import ecs_stores

            self.task = start_worker_thread(
                self.monitor_ecs_containers,
                params=ecs_stores,
                name="maintain-ecs-stores",
            )

        except ImportError:
            logger.warning(
                "unable to load ECS stores from pro module, "
                "declining to start monitoring loop"
            )

    def on_platform_shutdown(self):
        """Called when LocalStack is shutting down.

        Can be used to close any held resources (threads, processes, sockets, etc.).
        """
        logger.debug("shutting down ecs-traefik-compat extension")

        if self.task is not None:
            self.task.stop(quiet=False)

    def update_exception_handlers(self, handlers: CompositeExceptionHandler):
        """Called with the custom exception handlers of the LocalStack gateway.

        Overwrite this to add or update handlers.

        :param handlers: custom exception handlers of the gateway
        """
        try:
            from localstack.pro.core.services.ecs.task_executors.docker import (
                ECSTaskExecutorDocker,
            )

            ECSTaskExecutorDocker.after_container_run_hooks.append(
                self.fixup_container_state
            )

            logger.debug(
                "`fixup_container_state` injected into "
                "`ECSTaskExecutorDocker.after_container_run_hooks` successfully"
            )
            logger.debug(
                "ECSTaskExecutorDocker.after_container_run_hooks: %s",
                ECSTaskExecutorDocker.after_container_run_hooks,
            )

        except ImportError:
            logger.warning("unable to load docker ECS task executor from pro module")

    @staticmethod
    def fixup_container_state(config: TaskRunConfig, container: TaskContainer) -> None:
        """Update (or populate) the container's attachments and health status."""
        if container.container_config is None:
            return

        if (
            DOCKER_CLIENT.get_container_status(container.container_id)
            != DockerContainerStatus.UP
        ):
            return

        task_container = config.task.containers[config.container_config.container_index]

        if (
            config.task.last_status == "RUNNING"
            and config.task.desired_status == "RUNNING"
        ):
            task_container["healthStatus"] = "HEALTHY"

        interfaces = task_container.setdefault("networkInterfaces", [])

        container_ip = DOCKER_CLIENT.get_container_ipv4_for_network(
            container.container_id,
            container.container_config.network,
        )

        attachment = next(
            filter(
                lambda iface: container_ip == (iface.get("privateIpv4Address") or ""),
                interfaces,
            ),
            {"attachmentId": str(uuid4()), "privateIpv4Address": container_ip},
        )

        if attachment not in interfaces:
            interfaces.append(attachment)

        healthy = True

        for cnt in config.task.containers:
            bindings = cnt.setdefault("networkBindings", [])

            for binding in bindings:
                host_port = binding["hostPort"] = binding.get("containerPort") or binding.get("hostPort")

                if not host_port:
                    del binding["hostPort"]

            if cnt.get("healthStatus") != "HEALTHY":
                healthy = False

        if healthy:
            config.task.health_status = "HEALTHY"

        for definition in config.task.task_definition.container_definitions:
            for mapping in definition.setdefault("portMappings", []):
                host_port = mapping["hostPort"] = mapping.get("containerPort") or mapping.get("hostPort")

                if not host_port:
                    del mapping["hostPort"]

    @staticmethod
    def monitor_ecs_containers(stores: AccountRegionBundle) -> None:
        """Monitor the active tasks in all ECS stores and ensure the task and container health data is in sync."""
        while True:
            task: ECSTask
            store: ECSStore

            for account, region, store in stores.iter_stores():
                for cluster, tasks in store.tasks.items():
                    for task_arn, task in tasks.items():
                        healthy = True

                        for container in task.containers:
                            if container.get("healthStatus") != "HEALTHY":
                                healthy = False
                                container["healthStatus"] = "UNKNOWN"

                            if container.get("networkBindings") and not container.get(
                                "networkInterfaces"
                            ):
                                logger.warning(
                                    f"potentially faulty ECS Container: %s",
                                    json.dumps(
                                        {
                                            "cluster": cluster,
                                            "task_arn": task_arn,
                                            "container": container,
                                        },
                                        indent=2,
                                        default=str,
                                    ),
                                )

                        if healthy:
                            task.health_status = "HEALTHY"
                        else:
                            task.health_status = "UNKNOWN"

            time.sleep(10)
