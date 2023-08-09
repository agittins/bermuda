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

**STATUS: Pre-alpha! Only a basic service dumping info works right now.

This integration uses the advertisement data gathered by your esphome
bluetooth-proxy deployments to track or triangulate the relative
positions of any BLE or classic-bluetooth devices around your home
that are observed.

This can be used for prescence detection (ie which human/pet is at home
and in what room are they?), and device location (where's my
phone/toothbrush?)

It's unlikely to give you *fast* detection, but it might be handy to
supplement other sensors that can't distinguish between people. For
example, the mmWave sensor might turn on the lights, but a few
seconds later when Bermuda realises it's Alice, set her preferred
colour temperature. Or something.

If the tracking is any good (it might not be) it may even be possible
to calculate a vector for the person based on the last several seconds,
and *predict* which room they're heading for. I'm not smart enough to do
that so hopefully you're better at math than I am....

## Expectations

It's hard to say, but I wouldn't be expecting terribly
accurate locating, I think we'd be doing well to get down to room-level
granularity. It might only be possible to really get an idea of "very close
to this one esphome proxy" vs "somewhere between these three", but hopefully some
people smarter than me can contribute some algorithmic goodness that makes
it more useful.

## What you need

- HomeAssistant, with the `bluetooth` integration enabled
- Multiple (ideally) ESPHome devices, acting as `bluetooth_proxy` devices.
  I like the D1-Mini32 boards because they're cheap and easy to deploy.
- Some bluetooth things you want to locate (phones, beacons/tags etc)

## How it works

When a BLE device sends an advertisement packet, each bluetooth proxy that hears
it will send it to HomeAssistant, along with the `rssi` (received signal strength
indicator) which is basically how "strong" the received signal was.

Lots of things affect the rssi value, but one of them is distance. This integration
compares the rssi value for a given advertisement across the different
bluetooth proxies, and from that tries to make some guesses about how far
(in relative terms) the device was from each proxy.

From there we hope to get a rough idea of the transmitting device's location,
and perhaps even manage to map the device to a specific "Area" in homeassistant.

## What you'll see

After enabling the integration, you should start to see results for any bluetooth
devices in your home that are sending broadcasts. The implemented results are:
(important to note here that NONE of these boxes are ticked yet!)

[x] A raw listing of values returned when you call the `bermuda.dump_beacons` service
[] An interface to choose which devices should have sensors created for them
[] Sensors created for selected devices, showing their estimated location
[] A mud-map showing relative locations between proxies and detected devices
[] An interface to "pin" the proxies on a map to establish a sort of coordinate system
[] An interface to define Areas in relation to the pinned proxies

## TODO / Ideas

[x] Basic `bermuda.dump_beacons` service that responds with measurements.
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
integration, and I'm much more greybeard sysadmin than programmer, so if
I'm doing stupid things I really would welcome some improvements!

At this stage I'm using the service `bermuda.dump_beacons` to examine the
internal state while I gather the basic info and make initial efforts at
calculating locations. It's defined in `__init__.py`.

(right now that's about all that exists!)

## Prior Art

The `bluetooth_tracker` and `ble_tracker` integrations are only built to give a "home/not home"
determination, and don't do "Area" based location. (nb: "Zones" are places outside the
home, while "Areas" are rooms/areas inside the home). They feel rather "legacy" to me,
and they don't seem to be a popular target for innovation.

The "monitor" script uses standalone Pi's to gather bluetooth data and then pumps it into
MQTT. It doesn't use the `bluetooth_proxy` capabilities which I feel are the future of
home bluetooth networking (well, it is for my home, anyway!).

ESPrescence looks cool, but I don't want to dedicate my nodes to non-esphome use, and again
it doesn't leverage the bluetooth proxy features now in HA.

## Under the bonnet

The `bluetooth` integration doesn't really expose the advertisements that it receives,
expecting instead integrations to do specific tasks by device type. Even so, the data
available by the normal APIs only expose the view from one proxy - the one that received
the strongest signal (rssi) for that advertisement. We want to see the *relative* rssi
strengths for all the proxies, so we can then have a ham-fisted go at estimating their
position within the home.

To do this we need to directly access the bluetooth integration's data structures, where
it stores the recent adverts received by each proxy, along with the raw data and rssi.


**This component will set up the following platforms.**

| Platform        | Description                                                               |
| --------------- | ------------------------------------------------------------------------- |
| `binary_sensor` | Nothing yet.                                         |
| `sensor`        | Nor here, yet. |
| `switch`        | Nope.                                       |


## Installation

I'd strongly suggest installing via the HACS user interface, but no idea if that reliably works
yet :-) The instructions below are the generic notes from the template:

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
