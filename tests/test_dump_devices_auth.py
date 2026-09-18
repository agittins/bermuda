"""dump_devices lists every address the installation has heard, so a call that
carries a user must come from an administrator."""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from homeassistant.core import Context, ServiceCall
from homeassistant.exceptions import Unauthorized

from custom_components.bermuda.coordinator import BermudaDataUpdateCoordinator


@pytest.mark.asyncio
async def test_dump_devices_requires_an_admin_when_a_user_is_attached():
    users = {"admin": SimpleNamespace(is_admin=True), "guest": SimpleNamespace(is_admin=False)}

    async def async_get_user(user_id):
        return users.get(user_id)

    coordinator = SimpleNamespace(
        hass=SimpleNamespace(auth=SimpleNamespace(async_get_user=async_get_user)), devices={}
    )
    dump = BermudaDataUpdateCoordinator.service_dump_devices

    def call(user_id):
        return ServiceCall(coordinator.hass, "bermuda", "dump_devices", {}, Context(user_id=user_id))

    with pytest.raises(Unauthorized):
        await dump(coordinator, call("guest"))
    with pytest.raises(Unauthorized):
        await dump(coordinator, call("unknown-user"))

    # An administrator gets the dump, and so does a call with no user behind it
    # (an automation, a script, or the diagnostics download).
    assert await dump(coordinator, call("admin")) == {}
    assert await dump(coordinator, call(None)) == {}
