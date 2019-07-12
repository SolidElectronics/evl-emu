# evl-emu

This project is designed to allow Home Assistant to integrate with a DSC IT-100 integration module.  As it stands, there is no native IT-100 integration in HA yet, and this could still be useful in a situation where you don't have a serial port available to HA and would prefer an IP-based solution.

Fair warning, I haven’t tested this extensively and I offer no guarantees it will work, but I thought I’d share it in case it’s useful to someone. It’s written using the Python multiprocessing libraries so it spawns several processes that each perform one task. Ideally this would be rewritten in a cleaner way with asyncio but that’s currently beyond my ability.

I have this running under Raspbian on a rPi1 with the DSC panel connected via a serial-USB adapter at /dev/ttyUSB0, and a Home Asssistant instance inside Docker on another server.  You might want to use a udev rule or /dev/serial/by-id/whatever if you have multiple USB/Serial interfaces to ensure the correct one is used.

Note: This requires the ‘pyserial’ module for interacting with the serial port.


---
### Setup on Raspbian (different server than HA is running on)

#### Clone this repo

Assuming user pi on Raspbian here.

```
cd $HOME
git clone https://github.com/SolidElectronics/evl-emu.git
```

#### rc.local
```
/bin/su -c '/home/pi/evl-emu/evl-emu.py >/dev/null 2>&1' pi &
```
Change 'host' in envisalink.yaml to this system's IP address

---
### Setup on Hassbian (same server as HA)

#### rc.local
```
/bin/su -c '/home/homeassistant/.homeassistant/evl-emu.py >/dev/null 2>&1' homeassistant
```

Leave 'host' in envisalink.yaml as 127.0.0.1

---
### Home Assistant Setup
#### configuration.yaml
```
envisalink: !include envisalink.yaml
```

#### envisalink.yaml
Change 0000 to a valid code (for arming and disarming the panel)

I only included four zones here, and it probably only works with one partition.

The user_name and password are not used, but need to be present in the config file.
```
  host: 127.0.0.1
  panel_type: DSC
  user_name: user
  password: pass
  code: '0000'
  zones:
    1:
      name: 'Front door'
      type: 'door'
    2:
      name: 'Garage back door'
      type: 'door'
    3:
      name: 'Living Room Windows'
      type: 'window'
    4:
      name: 'Family Room Motion'
      type: 'motion'
#...
  partitions:
    1:
      name: 'Alarm'
```


The alarm zones need a name and a "type" the type comes from the list of [Device Classes](https://www.home-assistant.io/components/binary_sensor/) for binary sensors in Home Assistant

+ None: Generic on/off. This is the default and doesn’t need to be set.
+ battery: *On* means low, *Off* means normal
+ cold: *On* means cold, *Off* means normal
+ connectivity: *On* means connected, *Off* means disconnected
+ door: *On* means open, *Off* means closed
+ garage_door: *On* means open, *Off* means closed
+ gas: *On* means gas detected, *Off* means no gas (clear)
+ heat: *On* means hot, *Off* means normal
+ light: *On* means light detected, *Off* means no light
+ lock: *On* means open (unlocked), *Off* means closed (locked)
+ moisture: *On* means moisture detected (wet), *Off* means no moisture (dry)
+ motion: *On* means motion detected, *Off* means no motion (clear)
+ moving: *On* means moving, *Off* means not moving (stopped)
+ occupancy: *On* means occupied, *Off* means not occupied (clear)
+ opening: *On* means open, *Off* means closed
+ plug: *On* means device is plugged in, *Off* means device is unplugged
+ power: *On* means power detected, *Off* means no power
+ presence: *On* means home, *Off* means away
+ problem: *On* means problem detected, *Off* means no problem (OK)
+ safety: *On* means unsafe, *Off* means safe
+ smoke: *On* means smoke detected, *Off* means no smoke (clear)
+ sound: *On* means sound detected, *Off* means no sound (clear)
+ vibration: *On* means vibration detected, *Off* means no vibration (clear)
+ window: *On* means open, *Off* means closed
