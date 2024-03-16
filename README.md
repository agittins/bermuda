![Bermuda Logo](img/logo@2x.png)

# Bermuda BLE Trilateration

- Track bluetooth devices by Area in [HomeAssistant](https://home-assistant.io/), using [ESPHome](https://esphome.io/) [bluetooth_proxy](https://esphome.io/components/bluetooth_proxy.html) devices.

- (eventually) Triangulate device positions! Maybe.

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

## What it does:

- Area-based device location (ie, device-level room prescence) is working reasonably well.
- Creates sensors for Area and Distance for devices you choose
- Supports iBeacon devices, including those with randomised MAC addresses (like Android phones running HA Companion App)
- Creates `device_tracker` entities for chosen devices, which can be linked to "Person"s for Home/Not Home tracking
- Configurable settings for rssi reference level, environmental attenuation, max tracking radius
- Provides a comprehensive json/yaml dump of devices and their distances from each bluetooth
  receiver, via the `bermuda.dump_devices` service.

## What you need:

- One or more devices providing bluetooth proxy information to HA using esphome's `bluetooth_proxy` component.
  I like the D1-Mini32 boards because they're cheap and easy to deploy.
  The Shelly bluetooth proxy devices are reported to work well.
  Only natively-supported bluetooth devices are supported, meaning there's no current or planned support for MQTT devices etc.

- USB Bluetooth on your HA host is not ideal, since it does not timestamp the advertisement packets.
  However it can be used for simple "Home/Not Home" tracking, and Area distance support is enabled currently.

- Some bluetooth BLE devices you want to track. Smart watches, beacon tiles, thermometers etc

## What it does not do

- It does not (yet) provide location relative to multiple beacons, but that is the ultimate
  goal. RSSI is extremely "noisy" as data goes, but the hope is to find ways to smooth that
  out a bit and get a workable impression of where devices and scanners are in relation to each
  other. Math / Geometry geeks are very welcome to assist, I am well out of my depth here!

- As yet it doesn't know how to handle iPhones with their rotating MAC addresses, hopefully
  we can integrate with [Private BLE Device](https://www.home-assistant.io/integrations/private_ble_device/)
  to solve that. We do now support iBeacon, so companion apps such as the one for Android
  will now work, even with the rotating MAC-address. iBeacon apps on iOS behave oddly when
  backgrounded (an iOS-enforced oddity), so we don't support that either currently. We will
  have Private BLE working at some point though, watch this space.

## What you won't need (if this works for you)

- Bermuda provides equivalent functionality to `bluetooth_ble_tracker` by
  creating `device_tracker` entities for selected BLE devices.
  These can be used for Person home/away sensing.

- You might not need the `iBeacon` integration if you prefer how Bermuda handles
  beacons. See FAQ for more.

- You won't need separate devices dedicated to bluetooth sensing, as in you can deploy esp32 devices running esphome.
  These devices can also provide PIR motion sensors or other sensor functions, as well as bluetooth proxying
  for all sorts of other devices.
  Be careful not to have your esphome devices doing too many jobs though - the bluetooth stack is pretty demanding for
  the esphome, so you may have stability problems if you try too much (like streaming an esp32-cam!)

## How it Works

This integration uses the advertisement data gathered by your esphome or
Shelley bluetooth-proxy deployments into Homeassistant to track (and ultimately)
triangulate (more correctly, trilaterate) the relative positions of any BLE devices
observed around your home.

For now that means it can tell you which "Area" a device is closest to. In future it's hoped to
have it tell you "where" in your home a device is, in relative co-ordinates (ie, a map).

Note that this is more properly called "Tri*lateration*", as we are not
measuring the angles, but instead measuring distances. The bottom line
is that triangulation is more likely to hit people's search terms so we'll
probably bandy that term about a bit :-)

The integration gathers the advertisement data from the bluetooth integration,
and uses it to glean location/area info for all devices.

You can view the internal state of Bermuda by calling the `bermuda.dump_devices` service.

Currently it munges this into three entities for each tracked device:

- A `device_tracker` entity, which exposes a "home/not home" state. This entity can be mapped
  to a `person` to indicate if they are home (eg, by tracking their smart watch).
  This integration gives you two forms of presence tracking.

- An `Area` sensor. This gives the area name of the nearest bluetooth proxy. If you have a
  proxy in each room, you can use this to know which room a given device is currently in.

- A `Distance` sensor which gives the estimated distance from the nearest bluetooth proxy.
  This may help give a more relative indication of presence.

- A bunch of other fun sensors like "Distance from scanner x" and stuff - these are disabled by
  default because there are _many_ of them, and enabling lots WILL bog down your system.

Ultimately, it is hoped to also provide a mud-map of the home, where relative positions of
proxies and devices can be visually expressed. This assumes that we can (with some level of reliability)
compute the layout with trilateration. That is, by measuring the distances between devices and scanners it
is hoped to approximate a solution for the entire "network" of devices. This won't be lidar-level accurate,
but it might mean you'd only need proxies in every second room, say.

## Screenshots

After installing, the integration should be visible in Settings, Devices & Services

![The integration, in Settings, Devices & Services](img/screenshots/integration.png)

Press the `CONFIGURE` button to see the configuration dialog. At the bottom is a field
where you can enter/list any bluetooth devices the system can see. Choosing devices
will add them to the configured devices list and creating sensor entities for them.

![Bermuda integration configuration option flow](img/screenshots/configuration.png)

Choosing the device screen shows the current sensors and other info.

![Screenshot of device information view](img/screenshots/deviceinfo.png)

The sensor information also includes attributes area name and id, relevant MAC addresses
etc.

![Bermuda sensor information](img/screenshots/sensor-info.png)

In Settings, People, you can define any Bermuda device to track home/away status
for any person/user.

![Assign a Bermuda sensor for Person tracking](img/screenshots/person-tracker.png)

## FAQ

### Can I track my phone?

- Android: Yes! iPhone: Soon!? Bermuda now supports the iBeacon format, so if you can get your phone
  to broadcast iBeacon packets, then yes. The Homeassistant comanion app for
  Android does, so it works well.
  iPhone will be supported soon by tying in to the `Private BLE Device` integration.

- Bermuda's iBeacon support is rather simplistic and opinionated, reflecting the
  author somewhat.
  - To create a sensor for iBeacons, choose them in the `Configure` dialog where
    you can pick them from a drop-down, searchable list.
  - You'll probably want to rename the device and sensors to something sensible.
  - Bermuda considers every UUID/Major/Minor version to uniquely identify a given
    iBeacon. That means the MAC address can change, or you can have multiple beacons
    transmitting the same uuid/major/minor and they'll all be one single device - but
    don't do that though, IMO it's silly.
  - If your beacon sends multiple uuid's or changes it's major or minor version,
    they will show up as different "devices" that you can create sensors for. This
    might be good if you have one device that sends multiple IDs for some reason,
    or terrible if you have a device that tries to pack information into the major
    or minor fields. The latter device is, IMO, silly.
  - If there are known beacons (in reasonable numbers) that do something I thought
    was silly, I will consider adding support for them. I'd rather they don't exist
    though, and I think the iBeacon integration suffers because of its trying to
    support those cases.

### Why do my bluetooth devices have only the address and no name?

- you can tell your bluetooth proxies to send an inquiry in response to
  advertisements, this _might_ cause names to show up.
  In esphome, this is done by adding `active: true` to the
  `esp32_ble_tracker` section (this is separate from the active property of
  the `bluetooth_proxy` section, which controls outbound client connections).

  The default is `True`, and the templates at Ready-Made Projects also default to
  `True`.

  To be explicit in setting the option, the YAML should contain a section like
  this (there migth be extra paramaters, that's OK):

  ```
  esp32_ble_tracker:
    scan_parameters:
      active: True
  ```

- Also, when you first restart homeassistant after loading the integration it may
  take a minute or so for the system to collect the names of the devices it sees.

### Isn't mmWave better?

- mmWave is definitely _faster_, but it will only tell you "someone" has entered
  a space, while Bermuda can tell you _who_ (or what) is in a space.

### What about PIR / Infrared?

- PIR is also likely faster than bluetooth, but again it only tells you that
  someone / something is present, but doesn't tell you who/what.

So how does that help?

- If the home knows who is in a given room, it can set the thermostat to their
  personal preferences, or perhaps their lighting settings. This might be
  particularly useful for testing automations on yourself before unleashing them
  on to your housemates, so they don't get annoyed while you iron out the bugs :-)

- If you have BLE tags on your pets you can have automations specifically for them,
  and/or you can exclude certain automations, for example don't trigger a light from
  an IR sensor if it knows it's just your cat, say.

### How quickly does it react?

- There are three main factors.
  - First is how often your beacon transmits advertisements. Most are less than 2 seconds.
  - How short you have the `update_interval` set in the configuration. One second (or 0.9 if
    you like sliding windows) works pretty well on most systems, just check it doesn't impact
    your system load.
  - Whether your proxies are missing advertisement packets. In my esphome proxies I usually
    use these settings to ensure we don't miss toooo many:
    ```
    esp32_ble_tracker:
      interval: 1000ms # default 320ms. Time spent per adv channel
      window:   900ms # default 30ms. Time spent listening during interval.
    ```
    This makes sure the device is spending the majority of its time listening for
    bluetooth advertisements. 320/280 also works, but I think that 1000/900 gives a better
    balance of dedicated listening vs enough time for wifi tasks.

### How is the distance calculated?

- Currently, we use the relatively simple equation:
  `distance = 10 ** ((ref_power - rssi) / (10 * attenuation))`

  - `ref_power` is the rssi value you get when the device is 1 metre from the receiver.
    Currently you can configure this as a global setting in the options.
  - `rssi` is the "received signal strength indicator", being a measurement of RF power
    expressed in dB. rssi will usually range from -30 or so "up" to -100 or more. Numbers
    closer to zero are "stronger" or closer.
  - `attenuation` is a constant for the losses in the environment (humidity, air pressure(!),
    mammals etc). It's a "fudge factor". This is also set in the options. Experimentation with values
    ranging from 1.something to 3ish will usually get you somewhere.
  - `distance` is the resulting distance in metres.

- The values won't be suitable for all use-cases. Apart from the environmental factors we can't calculate
  (like walls, reflective surfaces etc), each device might transmit a different power level, and every transmitter
  and receiver might have antennae that perform differently. Because of this it is planned to allow separate
  calibration of scanners and devices to account for the variances.

### How do the settings work?

- `Max Radius` is used by the `device_tracker` and Area sensors to limit how far away a device can be while still
  considered to be in that location. For `device_tracker` purposes (this is the Home/Away sensor you can attach to a
  `Person`), it probably makes sense for this to be quite large - since you want to be "home" even if you're out
  in the yard or elsewhere that doesn't have good coverage. Bear in mind that "distance" is really a function of
  signal strength, so sitting in your car, inside the garage is going to show a much more distant signal than standing
  next to the car, say.
  For the `Area` sensor, a large `max_radius` may also make sense _if_ you have proxies in most of the rooms you want
  to track for. The Area will switch to the closest room, so you can consider it to mean "I am _near_ my office".
  However if you have proxies only in a few rooms, you might want a more definitive sense of "I am _not_ in my office",
  in which case you may wish to lower the max_radius to 4 metres or similar, so that being in the adjacent room doesn't
  show you as still being in your office. Note that results will probably be more reliable by putting a proxy in the
  adjacent room instead, but that depends on how many proxies you have.

- `Timeout` applies _only_ to the `device_tracker` integration. If no proxy has received a broadcast from the device
  in `timeout` seconds, that device will be marked as "not home" / Away. If you experience false triggers for being away
  (or more likely, false triggers for arriving home) then try increasing this value. Something like 300 (5 minutes) is
  probably fairly sensible for things where you want to automate arriving home. For things like automating an alert if
  a pet leaves the property a shorter value might be wiser, although I'd recommend using the Area and Distance sensors
  instead in that case, and choosing your own effective timeouts in the automation.

- `How often in seconds to update bluetooth data` or `update_interval` is usually best set at around 1.1 seconds.
  You can probably lower this for more responsive sensors, but it might impact your cpu usage.
  Using 1 second works well but if your proxies are sending updates every second (per the `interval` setting in esphome)
  and your device is advertising in multiples of 1 second, you might miss fresh adverts more often.
  Tweak to taste, at this stage my updates seem a bit more consistent with 1.1 versus 1.0.

- `How many samples to use for smoothing distance readings` or `smoothing_samples`. The bigger this number, the slower
  Bermuda will _increase_ distances for devices that are moving away. This is good for filtering noisy data (noise always
  makes the distance longer). 10 gives a moderately increasing distance, 20 seems pretty reliable. You can enable one
  of the "raw/unfiltered" sensor entities and graph them against the distance to get a feel for how well it works. Note that
  the smoothing algorithm will change over time, so don't get too attached to this setting, or spend too long tuning it!

- `Environment attenuation factor` is for "fudging" the rate at which the signal strength drops off with distance. In
  a vacuum with no other objects, the signal drops off predictably, but so would we. This factor helps to account for
  things like humidity, air density (altitude, pollution etc) and in some part the way that the surroundings might
  interfere with the signal. Basically we fiddle with this so that after we calibrate our 1 metre setting, we get a
  sensible result at other distances like at 4 metres etc. See the calibration section for how to do that.

- `Default RSSI at 1 metre` is how strong a signal the receiver sees when the transmitter is 1 metre away. Some beacons
  will actually advertise this figure in their data, and some of them might even be true. But ignore that. See the
  calibration section below for how to measure and set this for your own particular setup. Note that this number is
  dependent on the transmitter's software, the power it transmits at, the style (and orientation) of its antenna,
  the enclosure it's in, and depend on the receiver's enclosure, antenna and circuit's sensitivity. And everything in-
  between. Future versions of Bermuda will let you set the value per-device and offset it per-receiver, but for now,
  measure the device you care most about against the proxy you have the most of. And bear in mind that "distance" is
  really a relative term when trying to measure it using the amplitude of radio waves.

- `List of Bluetooth devices to specifically create tracking entities for` this split-infinitive lets you select which devices
  you want to track. Any advertising devices that are in range, and any iBeacons should be available to select in here.
  If you don't see any devices check that your esphome or other proxies are connected to Homeassistant and configured to
  relay bluetooth traffic.

### How do I choose values for Attenuation and Ref_Power?

- Soon you'll be able to set this per-device to account for variations in circuits, antennas and cases, but
  currently there are only the global defaults to fiddle with. Anyway, the idea is:
  - Place a transmitter 1 metre (just over 39 inches) from a scanner (bluetooth proxy)
  - In the attributes section of the transmitter's Distance sensor, watch the "Area rssi" value. Get a feel
    for what you consider to be an average. This will be your "Reference power at 1m" value.
  - Now move the transmitter away some distance (and measure that distance). Having a clear line-of-sight between
    the transmitter and the scanner is a good idea.
  - Now watch the "Distance" value. You want it to average around the right distance (but error towards a higher
    value, since a shorter measured distance is statistically less likely). That sentence is deliberately coy, since
    RF is a black art and I am not an ordained sorcerer. Also, some reflections might be in phase, most will not.
  - If the distance measured is always too high, decrease your attenuation figure. If it's too short, increase it.
    Repeat this procedure until you decide nothing works, the universe is pure chaos and it's time to give up.

## TODO / Ideas

- [ ] ~~Switch to performing updates on receipt of advertisements, instead of periodic polling~~ (nope, intervals work better)
- [ ] "Solve" realtime approximation of inter-proxy distances using Triangle Inequality
- [ ] Resolve x/y co-ordinates of all scanners and proxies (!)
- [ ] Some sort of map, just pick two proxies as an x-axis vector and go
- [ ] Config setting to define absolute locations of two proxies
- [ ] Support some way to "pin" more than two proxies/tags, and have it not break.
- [ ] An interface to define Areas in relation to the pinned proxies
- [ ] Experiment with some of
      [these algo's](https://mdpi-res.com/d_attachment/applsci/applsci-10-02003/article_deploy/applsci-10-02003.pdf?version=1584265508)
      for improving accuracy (too much math for me!). Particularly weighting shorter
      distances higher and perhaps the cosine similarity fingerprinting, possibly against
      fixed beacons as well to smooth environmental rssi fluctuations.

## Hacking tips

Wanna improve this? Awesome! Bear in mind this is my first ever HA
integration, and I'm much more greybeard sysadmin than programmer, so ~~if~~where
I'm doing stupid things I really would welcome some improvements!

You can start by using the service `bermuda.dump_devices` to examine the
internal state.

### Using `bermuda.dump_devices` service

Just calling the service `bermuda.dump_devices` will give you a full dump of the internal
data structures that bermuda uses to track and calculate its state. This can be helpful
for working out what's going on and troubleshooting, or to use if you have a very custom
need that you can solve with template sensors etc.

If called with no paramaters, the service will return all data. Paramaters are available
which let you limit or reformat the resulting data to make it easier to work with. In particular
the `addresses` paramater is helpful to only return data relevant for one or more MAC addresses
(or iBeacon UUIDs).
See the information on paramaters in the `Services` page in home assistant, under `Developer Tools`.

Important: If you decide to use the results of this call for your own templates etc, bear in mind that
the format might change in any release, and won't necessarily be considered a "breaking change".
This is beacuse the structure is used internally, rather than being a published API. That said, efforts will be made
to indicate in the release notes if fields in the structure are renamed or moved, but not for adding new
items.

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

## Installation

Definitely use the HACS interface! Once you have HACS installed, go to `Integrations`, click the
meatballs menu in the top right, and choose `Custom Repositories`. Paste `agittins/bermuda` into
the `Repository` field, and choose `Integration` for the `Category`. Click `Add`.

You should now be able to add the `Bermuda BLE Trilateration` integration. Once you have done that,
you need to restart Homeassistant, then in `Settings`, `Devices & Services` choose `Add Integration`
and search for `Bermuda BLE Trilateration`. It's possible that it will autodetect for you just by
noticing nearby bluetooth devices.

Once the integration is added, you need to set up your devices by clicking `Configure` in `Devices and Services`,
`Bermuda BLE Trilateration`.

In the `Configuration` dialog, you can choose which bluetooth devices you would like the integration to track.

The instructions below are the generic notes from the template:

1. Using the tool of choice open the directory (folder) for your HA configuration (where you find `configuration.yaml`).
2. If you do not have a `custom_components` directory (folder) there, you need to create it.
3. In the `custom_components` directory (folder) create a new folder called `bermuda`.
4. Download _all_ the files from the `custom_components/bermuda/` directory (folder) in this repository.
5. Place the files you downloaded in the new directory (folder) you created.
6. Restart Home Assistant
7. In the HA UI go to "Configuration" -> "Integrations" click "+" and search for "Bermuda BLE Trilateration"

<!---->

## Contributions are welcome!

If you want to contribute to this please read the [Contribution guidelines](CONTRIBUTING.md)

## Credits

This project was generated from [@oncleben31](https://github.com/oncleben31)'s [Home Assistant Custom Component Cookiecutter](https://github.com/oncleben31/cookiecutter-homeassistant-custom-component) template.

Code template was mainly taken from [@Ludeeus](https://github.com/ludeeus)'s [integration_blueprint][integration_blueprint] template
[Cookiecutter User Guide](https://cookiecutter-homeassistant-custom-component.readthedocs.io/en/stable/quickstart.html)\*\*

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
