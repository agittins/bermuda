# Bermuda BLE Triangulation

Triangulate your lost objects using ESPHome bluetooth proxies!

[![GitHub Release][releases-shield]][releases]
[![GitHub Activity][commits-shield]][commits]
[![License][license-shield]](LICENSE)

[![pre-commit][pre-commit-shield]][pre-commit]
[![Black][black-shield]][black]

[![hacs][hacsbadge]][hacs]
[![Project Maintenance][maintenance-shield]][user_profile]
[![BuyMeCoffee][buymecoffeebadge]][buymecoffee]

[![Discord][discord-shield]][discord]
[![Community Forum][forum-shield]][forum]

**STATUS: Early days!
- Can replace bluetooth_ble_tracker by creating entities for home/not_home
  for selected BLE devices, which can be used for Person home/away sensing.
  This is the "Zone" element of homeassistant localisation, where "home" is
  one Zone, and "work" or other large geographic areas might be others.

- Provides a json/yaml dump of devices and their distances from each bluetooth
  receiver. This is via the `bermuda.dump_devices` service.

- (soon) Provides sensors to indicate which Room ("Area", in HA terms) a device
  is "in". This is based on the measured RF power (rssi - received signal strength
  indicator) which can give a (varyingly inaccurate) measure of distance to the
  closest BLE Proxy. If you have a bluetooth receiver (ESPHome with `bluetooth_proxy`
  or a Shelley device) in each room you want tracking for, this will do the job.

- (soon) Provide a mud-map of your entire home, in bluetooth signal strength terms.

This integration uses the advertisement data gathered by your esphome or
Shelley bluetooth-proxy deployments to track or triangulate (more correctly,
trilaterate) the relative positions of any BLE devices
observed around your home.

Note that this is more properly called "Tri*lateration*", as we are not
measuring the angles, but instead measuring distances. The bottom line
is that triangulation is more likely to hit people's search terms.

This integration gives you two forms of presence tracking.
- Simple Home/Away detection using the device_tracker integration. This is
  not much different to the already working bluetooth_le_tracker integration
  in that regard, but was an easy step along the way to...
- Room-based ("Area"s in homeassistant parlance) localisation for bluetooth
  devices. For example, "which human/pet is at home and in what room are they?"
  and "where's my phone/toothbrush?"

## FAQ
Isn't mmWave better?
: mmWave is definitely *faster*, but it will only tell you "someone" has entered
a space, while Bermuda can tell you *who* is in a space.
What about PIR / Infrared?
: It's also likely faster than bluetooth, but again it only tells you that
someone / something is present, but doesn't tell you who/what.

So how does that help?
: If the home knows who is in a given room, it can set the thermostat to their
personal preferences, or perhaps their lighting settings. This might be
particularly useful for testing automations for yourself before unleashing them
on to your housemates, so they don't get annoyed while you iron out the bugs :-)
: If you have BLE tags on your pets you can have automations specifically for them,
and/or you can exclude certain automations, for example don't trigger a light from
an IR sensor if it knows it's just your cat, say.

How quickly does it react?
: That will mainly depend on how often your beacon transmits advertisements, however
right now the integration only re-calculates on a timed basis. This should be changed
to a realtime recalculation based on incoming advertisements soon.


## What you need

- HomeAssistant, with the `bluetooth` integration enabled
- Multiple (ideally) ESPHome devices, acting as `bluetooth_proxy` devices.
  I like the D1-Mini32 boards because they're cheap and easy to deploy.
  The Shelly bluetooth proxy devices should also work but I don't have any
  so can't test them myself. Issue reports with debug info welcome.
- Some bluetooth things you want to locate (phones, beacons/tags etc)
- That's it! No mqtt, or devices dedicated to bluetooth (the esphome devices
  can also provide other sensors etc, within reason)

## How it works

When a BLE device sends an advertisement packet, each bluetooth proxy that hears
it will send it to HomeAssistant, along with the `rssi` (received signal strength
indicator) which is basically how "strong" the received signal was.

Lots of things affect the rssi value, but one of them is distance. This integration
compares the rssi value for a given advertisement across the different
bluetooth proxies, and from that tries to make some guesses about how far
(in relative terms) the device was from each proxy.

The plan is to experiment with multiple algorithms to find the best ways to
establish a device's location. In the first instace the methods are:
- If a device is close (within a few metres) to a receiver, consider it to be in
  the same Area as that receiver. (Working)
- Attempt to "solve" a 2D map for all beacons and receivers based on the triangles
  created between them to derive all the required distances. (WIP)

## What you'll see

After enabling the integration, you should start to see results for any bluetooth
devices in your home that are sending broadcasts. The implemented results are:
(important to note here that VERY FEW of these boxes are ticked yet!)

[x] A raw listing of values returned when you call the `bermuda.dump_devices` service
    [x] `area` if a device is within a max distance of a receiver
[] An interface to choose which devices should have sensors created for them
[x] Sensors created for selected devices, showing their estimated location
[] Algo to "solve" the 2D layout of devices
[] A mud-map showing relative locations between proxies and detected devices
[] An interface to "pin" the proxies on a map to establish a sort of coordinate system
[] An interface to define Areas in relation to the pinned proxies

## TODO / Ideas

[x] Basic `bermuda.dump_devices` service that responds with measurements.
[] Switch to performing updates on receipt of advertisements, instead of periodic polling
[] Realtime approximation of inter-proxy distances using Triangle Inequality
[] Resolve x/y co-ordinates of all scanners and proxies (!)
[] Some sort of map, just pick two proxies as an x-axis vector and go
[] Config setting to define absolute locations of two proxies
[] Support some way to "pin" more than two proxies/tags, and have it not break.
[] Create entities (use `device_tracker`? or create own?) for each detected beacon
[] Experiment with some of
   [these algo's](https://mdpi-res.com/d_attachment/applsci/applsci-10-02003/article_deploy/applsci-10-02003.pdf?version=1584265508)
   for improving accuracy (too much math for me!). Particularly weighting shorter
   distances higher and perhaps the cosine similarity fingerprinting, possibly against
   fixed beacons as well to smooth environmental rssi fluctuations.


## Hacking tips

Wanna improve this? Awesome! Here's some tips on how it works inside and
what direction I'm hoping to go. Bear in mind this is my first ever HA
integration, and I'm much more greybeard sysadmin than programmer, so ~~if~~where
I'm doing stupid things I really would welcome some improvements!

At this stage I'm using the service `bermuda.dump_devices` to examine the
internal state while I gather the basic info and make initial efforts at
calculating locations. It's defined in `__init__.py`.

(right now that's about all that exists!)

## Prior Art

The `bluetooth_tracker` and `ble_tracker` integrations are only built to give a "home/not home"
determination, and don't do "Area" based location. (nb: "Zones" are places outside the
home, while "Areas" are rooms/areas inside the home). I wanted to be free to experiement with
this in ways that might not suit core, but hopefully at least some of this could find
a home in the core codebase one day.

The "monitor" script uses standalone Pi's to gather bluetooth data and then pumps it into
MQTT. It doesn't use the `bluetooth_proxy` capabilities which I feel are the future of
home bluetooth networking (well, it is for my home, anyway!).

ESPrescence looks cool, but I don't want to dedicate my nodes to non-esphome use, and again
it doesn't leverage the bluetooth proxy features now in HA. I am probably reinventing
a fair amount of ESPrescense's wheel.

**This component will set up the following platforms.**

| Platform        | Description                                                               |
| --------------- | ------------------------------------------------------------------------- |
| `binary_sensor` | Nothing yet.                                         |
| `sensor`        | Nor here, yet. |
| `switch`        | Nope.                                       |


## Installation

Definitely use the HACS interface! Once you have HACS installed, go to `Integrations`, click the
meatballs menu in the top right, and choose `Custom Repositories`. Paste `agittins/bermuda` into
the `Repository` field, and choose `Integration` for the `Category`. Click `Add`.

You should now be able to add the `Bermuda BLE Triangulation` integration. Once you have done that,
you need to restart Homeassistant, then in `Settings`, `Devices & Services` choose `Add Integration`
and search for `Bermuda BLE Triangulation`.

The instructions below are the generic notes from the template:

1. Using the tool of choice open the directory (folder) for your HA configuration (where you find `configuration.yaml`).
2. If you do not have a `custom_components` directory (folder) there, you need to create it.
3. In the `custom_components` directory (folder) create a new folder called `bermuda`.
4. Download _all_ the files from the `custom_components/bermuda/` directory (folder) in this repository.
5. Place the files you downloaded in the new directory (folder) you created.
6. Restart Home Assistant
7. In the HA UI go to "Configuration" -> "Integrations" click "+" and search for "Bermuda BLE Triangulation"


<!---->

## Contributions are welcome!

If you want to contribute to this please read the [Contribution guidelines](CONTRIBUTING.md)

## Credits

This project was generated from [@oncleben31](https://github.com/oncleben31)'s [Home Assistant Custom Component Cookiecutter](https://github.com/oncleben31/cookiecutter-homeassistant-custom-component) template.

Code template was mainly taken from [@Ludeeus](https://github.com/ludeeus)'s [integration_blueprint][integration_blueprint] template
[Cookiecutter User Guide](https://cookiecutter-homeassistant-custom-component.readthedocs.io/en/stable/quickstart.html)**

---

[integration_blueprint]: https://github.com/custom-components/integration_blueprint
[black]: https://github.com/psf/black
[black-shield]: https://img.shields.io/badge/code%20style-black-000000.svg?style=for-the-badge
[buymecoffee]: https://www.buymeacoffee.com/AshleyGittins
[buymecoffeebadge]: https://img.shields.io/badge/buy%20me%20a%20coffee-donate-yellow.svg?style=for-the-badge
[commits-shield]: https://img.shields.io/github/commit-activity/y/agittins/bermuda.svg?style=for-the-badge
[commits]: https://github.com/agittins/bermuda/commits/main
[hacs]: https://hacs.xyz
[hacsbadge]: https://img.shields.io/badge/HACS-Custom-orange.svg?style=for-the-badge
[discord]: https://discord.gg/Qa5fW2R
[discord-shield]: https://img.shields.io/discord/330944238910963714.svg?style=for-the-badge
[exampleimg]: example.png
[forum-shield]: https://img.shields.io/badge/community-forum-brightgreen.svg?style=for-the-badge
[forum]: https://community.home-assistant.io/
[license-shield]: https://img.shields.io/github/license/agittins/bermuda.svg?style=for-the-badge
[maintenance-shield]: https://img.shields.io/badge/maintainer-%40agittins-blue.svg?style=for-the-badge
[pre-commit]: https://github.com/pre-commit/pre-commit
[pre-commit-shield]: https://img.shields.io/badge/pre--commit-enabled-brightgreen?style=for-the-badge
[releases-shield]: https://img.shields.io/github/release/agittins/bermuda.svg?style=for-the-badge
[releases]: https://github.com/agittins/bermuda/releases
[user_profile]: https://github.com/agittins
