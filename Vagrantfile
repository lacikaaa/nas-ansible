# -*- mode: ruby -*-
# vi: set ft=ruby :

VAGRANTFILE_API_VERSION = "2"

# to activate vagrant specific settings issue:
# export ANSIBLE_ARGS='--extra-vars "vagrant=true"'

Vagrant.configure(VAGRANTFILE_API_VERSION) do |config|
    config.vm.box = "opensuse/openSUSE-42.3-x86_64"
    
    config.vbguest.iso_path = File.expand_path("/Applications/VirtualBox.app/Contents/MacOS/VBoxGuestAdditions.iso", __FILE__)
    config.vbguest.no_remote = true

    config.vm.provider "virtualbox" do |vb|
        vb.memory = 2048
        vb.cpus = 2

        root2 = File.realpath( "." ).to_s + "/diskroot2.vdi"
        data1 = File.realpath( "." ).to_s + "/diskdata1.vdi"
        data2 = File.realpath( "." ).to_s + "/diskdata2.vdi"

        vb.customize ['createhd', '--filename', root2, '--size', 5 * 1024]
        vb.customize ['createhd', '--filename', data1, '--size', 2 * 1024]
        vb.customize ['createhd', '--filename', data2, '--size', 2 * 1024]

        vb.customize ['storageattach', :id, '--storagectl', 'SATA Controller', '--port', 1, '--device', 0, '--type', 'hdd', '--medium', root2]
        vb.customize ['storageattach', :id, '--storagectl', 'SATA Controller', '--port', 2, '--device', 0, '--type', 'hdd', '--medium', data1]
        vb.customize ['storageattach', :id, '--storagectl', 'SATA Controller', '--port', 3, '--device', 0, '--type', 'hdd', '--medium', data2]
    end

    config.ssh.insert_key = false

    config.vm.provision "ansible" do |ansible|
        ansible.verbose = "v"
        ansible.playbook = "nas.yml"
        ansible.raw_arguments = Shellwords.shellsplit(ENV['ANSIBLE_ARGS']) if ENV['ANSIBLE_ARGS']
    end
end