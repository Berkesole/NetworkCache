#!/bin/sh
set -eu
ip -o link show
tc qdisc show
ip route show
ip -s link show
