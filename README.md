# nas-ansible
NAS ansible configuration

# Testing

Check `Vagrantfile`
To activate vagrant specific settings issue: `export ANSIBLE_ARGS='--extra-vars "vagrant=true"'`
Guest addiotion ISO hardcoded for Ubuntu

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