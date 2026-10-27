# nas-ansible
NAS ansible configuration

# Testing

Check `Vagrantfile`
To activate vagrant specific settings issue: `export ANSIBLE_ARGS='--extra-vars "vagrant=true"'`
Guest addiotion ISO hardcoded for Ubuntu

## Prereq
`vagrant plugin install vagrant-vbguest`

## Run
`vagrant up`

## Debug
`vagrant ssh` + logs

# UPS configuration with Network UPS Tools
Configuring FSP EP1000 with nut

Run role with:
```
ansible-playbook -i inventory/inventory.yml ups.yml
```
Based on:
- https://networkupstools.org/documentation.html
- https://en.opensuse.org/SDB:Network_UPS_Tools
- https://hup.hu/node/124296 [HUN]
- http://manpages.ubuntu.com/manpages/bionic/man8/blazer_usb.8.html

Change the passwords under: `roles/nut/vars/passwords.yml`

# HA monitoring
The `ha-monitor` role installs a Mosquitto broker (compose project in `/data/mosquitto`, listening on 127.0.0.1:1883 only)
and `nas-ha-agent`, a systemd timer (every minute) that publishes NAS status to Home Assistant over MQTT.

Every broker client has its own user: `nas-agent`, `homeassistant` and `gpb`. `templates/acl.j2` lists the topics
each one may use. Containers reach the broker as `mosquitto:1883` over the shared Docker network `mqtt`
(172.30.0.0/24, internal, created by the role). To join a compose project to it, add `networks: [default, mqtt]` to the
service and this top-level block:
```
networks:
  mqtt:
    external: true
```

Create the password file from the example and set one password per user:
```
cp roles/ha-monitor/vars/mqtt_password.yml.example roles/ha-monitor/vars/mqtt_password.yml
```

Run role with:
```
.venv/bin/ansible-playbook -i inventory/inventory.yml ha-monitor.yml
```
`/data` must be unlocked first (`mount-data-drives`), the broker and its config live there.
