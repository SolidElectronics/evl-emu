# evl-emu

This project is designed to allow Home Assistant to integrate with a DSC IT-100 integration module.  As it stands, there is no native IT-100 integration in HA yet, and this could still be useful in a situation where you don't have a serial port available to HA and would prefer an IP-based solution.

Fair warning, I haven’t tested this extensively and I offer no guarantees it will work, but I thought I’d share it in case it’s useful to someone. It’s written using the Python multiprocessing libraries so it spawns several processes that each perform one task. Ideally this would be rewritten in a cleaner way with asyncio but that’s currently beyond my ability.

I have this running under Raspbian on a rPi1 with the DSC panel connected via a serial-USB adapter at /dev/ttyUSB0, and a Home Asssistant instance inside Docker on another server.  You might want to use a udev rule or /dev/serial/by-id/whatever if you have multiple USB/Serial interfaces to ensure the correct one is used.

Note: This requires the ‘pyserial’ module for interacting with the serial port.


---
### Setup on Raspbian (diffent server than HA is running on)

#### rc.local
```
/bin/su -c '/home/pi/evl-emu.py >/dev/null 2>&1' pi &
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
### Home Assistand Setup
#### configuration.yaml
```
envisalink: !include envisalink.yaml
```

#### envisalink.yaml
Change 0000 to a valid code (for arming and disarming the panel)

I only included two zones here, and it probably only works with one partition.

user_name and password are not used, but need to be present in the config file.
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
#...
  partitions:
    1:
      name: 'Alarm'
```
