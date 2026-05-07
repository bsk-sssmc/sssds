#!/bin/sh
# Installed by provision-node.sh as /lib/systemd/system-shutdown/sssds-wol-persist
#
# Runs at the very end of the shutdown sequence (just before the kernel
# halts) and re-applies the Wake-on-LAN bit to every non-loopback NIC.
#
# Why: some hardware (notably Apple-built boxes running Linux) loses the
# WOL bit when the interface is brought down during shutdown. Setting it
# again at the last possible moment puts the firmware into the right
# state to respond to a magic packet while the system is powered off.

case "$1" in
  poweroff|halt)
    for IF in $(ls /sys/class/net 2>/dev/null); do
      [ "$IF" = "lo" ] && continue
      /sbin/ethtool -s "$IF" wol g 2>/dev/null || true
    done
    ;;
esac
