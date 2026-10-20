# Reserved interfaces and ports

Upstream: `upstream-sdk-4.6.0/appdevguide_reserved-interfaces.md`. Machine-readable copy used by
the checker: `tools/data/reserved-interfaces.json` (regenerate if the upstream doc changes).

## Denied / restricted plugs and slots (summary)

Denied plugs (no exceptions): account-control, snapd-control, block-devices, raw-volumes,
udisks2 (plug+slot), docker, docker-support (plug+slot), acrn-support, bool-file,
**browser-support**, classic-support, core-support, cpu-control, daemon-notify, dm-crypt,
**firewall-control**, fpga, fuse-support, fwupd, gconf, gpg-keys, gpio-control,
gpio-memory-control, greengrass-support, hardware-random-control, hidraw, hugepages-control,
intel-mei, io-ports-control, ion-memory-control, kernel-module-control, kernel-module-load,
kubernetes-support, kvm, libvirt, login-session-control, lxd, lxd-support,
microstack-support, multipass-support, netlink-audit, netlink-connector, netlink-driver,
**network-control, network-manager, network-manager-observe, network-setup-control,
network-setup-observe**, packagekit-control, password-manager-service, personal-files,
physical-memory-control, physical-memory-observe, polkit-interface, process-control,
scsi-generic, sd-control, snap-refresh-control, ssh-keys, storage-framework-service,
system-backup, system-trace, tee, tpm, uhid, uinput, home.

Restricted:
* `system-files` – only `/dev/shm` paths.
* `shared-memory` – only `shared-memory: datalayer-shm`.
* `mount-control` – only for the storage-extension concept (`upstream-sdk-4.6.0/storage-extension.md`).
* content **plugs** `package-certificates`, `package-run`, `rda-proxy-service` and content
  **slots** `active-solution`, `auth-service`, `rda-proxy-service` are reserved for the system.
  (The template's `package-run` *slot* and `active-solution` *plug* are the allowed directions.)

The doc lists `use-support` and `udisk2` – typos for `fuse-support` and `udisks2`; the JSON
contains both spellings.

## Blocked ports

ctrlX OS: 22, 80, 81, 443, 1338, 1880, 1900, 2069, 2070, 4840, 5353, 5355, 5858, 6000, 7878, 8069, 11740, 11741
Other apps: 1881, 1884, 1885, 2883, 4222, 6123, 7505, 8000, 8080, 8088, 8142, 8840, 8883,
9230, 9240, 10123, 16620, 16700, 16701, 16800, 16810, 18500, 47808, 48898, 49250–50250,
51218, 56090, 58000

(Note 8080 is blocked on the device; the template uses 8080 only for the local dev server
outside the snap.)

## If you really need an interface or port

1. Look for an alternative (Unix socket instead of port, content interface, REST API of the OS).
2. Add it to `snapcraft.yaml` **and** `validation/slotplug-description.json` / `portlist-description.json`
   with a ≥ 16-char purpose. Ports: robust against "port in use", secure protocol by default.
3. Ask the human. For Bosch-validated apps, the justification goes to the validation team.
