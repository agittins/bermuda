# External Bluetooth identities

The `bermuda.external_identity` Python API lets another local integration resolve
a protocol-specific identity and associate its observed Bluetooth addresses with
one stable Bermuda device. The resolver owns matching and its secrets. Bermuda
owns scanner observations, RSSI, distance calculation and area selection.

`API_VERSION` is `1`. Check `async_is_available(hass)` after Bermuda loads, then:

```python
from custom_components.bermuda.external_identity import async_register_identity

identity = await async_register_identity(hass, config_entry, "stable_id", "Accessory")
await identity.async_observe_address(address, service_info.time)
```

The owner config entry controls the handle's lifetime. Registration creates
`external:<owner domain>:<stable_id>` as its stable Bermuda identity. The logical
identity has a Bermuda registry identifier and no synthetic Bluetooth connection
or IRK. Register once per identity, not once per address rotation. Names and IDs
are limited to 128 characters. Renames keep the same stable ID. A separate config
entry cannot take over an identity during a Bermuda coordinator lifetime.

Call `async_observe_address` only after matching a fresh, actual local observation.
Pass the original Home Assistant Bluetooth **monotonic** timestamp, never wall
time, the current polling time or an address predicted from keys. Regressing,
future, nonfinite and more-than-180-second-old observations are rejected. One
address cannot simultaneously belong to two external identities.

Bermuda uses its real per-scanner observations at or after the association start.
The scanner source, RSSI and timestamp are preserved. The identity has separate
bounded smoothing histories; it never imports a source's earlier history. API v1
excludes scanners without original timestamps, including USB observations for
which Bermuda synthesizes a timestamp. Those scanners remain available for
Bermuda's existing native device tracking.

Associations expire at most 180 seconds after the last supplied observation.
`await identity.async_remove_address(address)` immediately severs one address;
`await identity.async_remove()` severs all of them and invalidates the handle.
Removing or unloading the owner does the same. Unloading Bermuda invalidates
every handle; a resolver must register again after it loads. External last-area
sensors never use restored observations as current or last-area evidence.

There are at most 256 registered logical identities per coordinator lifetime,
32 current/recent address slots per identity and 256 scanner pairs per identity.
`BermudaUnavailable` means the handle/provider lifetime ended. Other
`ExternalIdentityError` failures reject that request while retaining a valid
handle; the owner should not abandon a live handle and try to register over it.
Resolvers must handle ambiguity before publishing an address and must not treat
BLE identity matches as authenticated distance, anti-replay or security authority.
