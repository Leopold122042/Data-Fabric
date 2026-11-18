#!/bin/sh
set -eu

IPTABLES=/usr/sbin/iptables
CHAIN=MEDFABRIC_CAMPUS

if [ ! -x "$IPTABLES" ]; then
  echo "iptables is not available at $IPTABLES" >&2
  exit 1
fi

if [ "${1:-apply}" = "--remove" ]; then
  while "$IPTABLES" -w -C INPUT -p tcp --dport 8321 -j "$CHAIN" 2>/dev/null; do
    "$IPTABLES" -w -D INPUT -p tcp --dport 8321 -j "$CHAIN"
  done
  "$IPTABLES" -w -F "$CHAIN" 2>/dev/null || true
  "$IPTABLES" -w -X "$CHAIN" 2>/dev/null || true
  exit 0
fi

if ! "$IPTABLES" -w -S "$CHAIN" >/dev/null 2>&1; then
  "$IPTABLES" -w -N "$CHAIN"
fi
"$IPTABLES" -w -F "$CHAIN"
"$IPTABLES" -w -A "$CHAIN" -s 172.28.64.0/18 -j ACCEPT
"$IPTABLES" -w -A "$CHAIN" -s 114.212.160.0/20 -j ACCEPT
"$IPTABLES" -w -A "$CHAIN" -j DROP

if ! "$IPTABLES" -w -C INPUT -p tcp --dport 8321 -j "$CHAIN" 2>/dev/null; then
  "$IPTABLES" -w -I INPUT 1 -p tcp --dport 8321 -j "$CHAIN"
fi
