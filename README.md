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

# Firewall
The `firewall` role loads its own nftables table, `inet nasfw` (`/etc/nasfw.nft`, `nasfw.service`). It never runs
`flush ruleset`, because Docker's iptables-nft tables share the same ruleset. One prerouting chain filters everything that
arrives on a physical interface. It runs before Docker's DNAT, so host services and published container ports are
handled by the same rules. Docker bridges and lo are not filtered. That includes WireGuard clients, which the container masquerades.

Ports are listed in `roles/firewall/defaults/main.yml`. `firewall_public_ports` must match the router's port forwards.
`firewall_lan_ports` are open to 192.168.174.0/24 only. Everything else is dropped and logged:
```
sudo journalctl -k -g nasfw-drop
sudo nft list chain inet nasfw ingress
```

Run role with:
```
.venv/bin/ansible-playbook -i inventory/inventory.yml firewall.yml
```
Before a change is loaded, a `nasfw-revert` timer is armed. If ansible cannot open a new ssh connection afterwards, the
timer runs `systemctl disable --now nasfw.service` after 5 minutes, which leaves the NAS without a firewall until the
rules are fixed.
