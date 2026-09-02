from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field

from fastapi import APIRouter
from pydantic import BaseModel

from app.jobs.base import JobExecutor

RegisterJobExecutor = Callable[[JobExecutor], JobExecutor]


@dataclass(frozen=True)
class BusinessRouteMount:
    router: APIRouter
    prefix: str = ""


@dataclass
class BusinessRouteCollector:
    _mounts: list[BusinessRouteMount] = field(default_factory=list)

    def include_router(self, router: APIRouter, *, prefix: str = "") -> None:
        if prefix and not prefix.startswith("/"):
            raise ValueError("business package route prefix must start with /")
        self._mounts.append(BusinessRouteMount(router=router, prefix=prefix))

    def route_mounts(self) -> tuple[BusinessRouteMount, ...]:
        return tuple(self._mounts)


@dataclass(frozen=True)
class BusinessPackage:
    name: str
    register: Callable[[RegisterJobExecutor], None]
    register_routes: Callable[[BusinessRouteCollector], None] | None = None
    requires_object_storage: bool = False
    schemas: tuple[type[BaseModel], ...] = ()
