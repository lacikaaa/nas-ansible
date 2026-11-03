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

# Journal retention (journald)
The `journald` role writes `/etc/systemd/journald.conf.d/retention.conf`: archived journal files that start more than
`journald_max_retention` (13 months) ago are removed, which keeps at least the last 12 months, and `/var/log/journal` is
capped at `journald_system_max_use` (2G) in case something starts logging much more. Without it journald keeps logs
until it reaches its default 4G cap. journald is restarted when the file changes, and it deletes the files that exceed the new
limits at that point.

Run role with:
```
.venv/bin/ansible-playbook -i inventory/inventory.yml journald.yml
```

# CPU history (sysstat)
The `sysstat` role installs sysstat. `sysstat-collect.timer` samples system-wide CPU, load, memory, disk and network every
10 minutes into `/var/log/sa`, kept for `sysstat_history_days` (28). Read it with:
```
sar -u -f /var/log/sa/saDD -s 02:00:00 -e 06:00:00   # CPU; -q load, -r memory, -d disks
```
sar has no per-process data. For that, `pidstat-night.timer` runs `pidstat -u -h -l` every night, by default from 01:00
to 07:00 with 60 s samples (`pidstat_night_*` in `roles/sysstat/defaults/main.yml`), into
`/var/log/pidstat/night-YYYYMMDD.log`. Logs older than 14 days are removed by `systemd-tmpfiles-clean`. Set
`pidstat_night_enabled: false` to remove the timer again.

Run role with:
```
.venv/bin/ansible-playbook -i inventory/inventory.yml sysstat.yml
```

# btrfs space cache v2
`btrfs-space-cache-v2.yml` is a one-off that moves `/` and `/data` from the deprecated space cache v1 to v2 (free space
tree). The kernel builds the tree only on a fresh read-write mount, so the playbook reboots the NAS, with no package update.
It adds `space_cache=v2` to the `/` line in `/etc/fstab`, and `mount-data-drives` mounts `/data` with the same option.
After the reboot it checks `compat_ro_flags` in both superblocks. Wait for a running scrub or balance to finish first;
the playbook stops if one is running. The first `/data` mount takes longer while the tree is built. Don't interrupt it.
```
.venv/bin/ansible-playbook -i inventory/inventory.yml btrfs-space-cache-v2.yml
```
Once the tree exists it is a filesystem feature and is used without the mount option. To undo it, first remove
`space_cache=v2` from the `/` line in `/etc/fstab` and from `mount-data-drives.j2`, otherwise the next mount builds the tree
again. Then run `btrfs check --clear-space-cache v2 <device>` on the unmounted filesystem: for `/data` before
`mount-data-drives` runs, for `/` from rescue media. Mount it once with `space_cache=v1`.
